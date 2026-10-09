"""Command line: run the MCP server, or manage and query docs from a shell.

devdocs-mcp                                  serve over stdio (what MCP clients launch)
devdocs-mcp serve --transport http           serve over streamable HTTP on :8000/mcp
devdocs-mcp install python~3.12 javascript   download docs for offline use
devdocs-mcp search getcwd -d python          search like the MCP tool does
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

import anyio

from . import __version__
from .store import DevDocsError, DevDocsStore, Settings

log = logging.getLogger("devdocs_mcp")

COMMANDS = {
    "serve", "list", "catalog", "install", "remove", "update",
    "search", "fulltext", "read", "entries",
}  # fmt: skip


def _split_docs(value: str | None) -> list[str]:
    return [doc for doc in re.split(r"[\s,]+", value or "") if doc]


def build_parser() -> argparse.ArgumentParser:
    # Accepted before or after the command. SUPPRESS keeps a subcommand's
    # defaults from overwriting a value given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Where docs are stored (env DEVDOCS_DATA_DIR; default ~/.local/share/devdocs-mcp).",
    )
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Log debug output.",
    )

    parser = argparse.ArgumentParser(
        prog="devdocs-mcp",
        description="DevDocs API documentation, offline, as an MCP server.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    serve = sub.add_parser("serve", parents=[common], help="Run the MCP server (the default).")
    serve.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http", "sse"],
        default=os.environ.get("DEVDOCS_TRANSPORT") or "stdio",
        help="stdio for clients that launch the server; http to serve on --host:--port/mcp "
        "(env DEVDOCS_TRANSPORT; default stdio).",
    )
    serve.add_argument(
        "--host",
        default=os.environ.get("DEVDOCS_HOST") or "127.0.0.1",
        help="HTTP bind address (env DEVDOCS_HOST; default 127.0.0.1).",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DEVDOCS_PORT") or 8000),
        help="HTTP port (env DEVDOCS_PORT; default 8000).",
    )
    serve.add_argument(
        "--preload",
        default=os.environ.get("DEVDOCS_PRELOAD", ""),
        help="Docs to install in the background at startup, e.g. 'python,javascript' "
        "(env DEVDOCS_PRELOAD).",
    )

    sub.add_parser("list", parents=[common], help="List installed docs.")

    catalog = sub.add_parser("catalog", parents=[common], help="List docs DevDocs offers.")
    catalog.add_argument("query", nargs="?", default="", help="Filter, e.g. 'python'.")
    catalog.add_argument("--refresh", action="store_true", help="Re-download the catalog.")

    install = sub.add_parser("install", parents=[common], help="Download docs for offline use.")
    install.add_argument("docs", nargs="+", help="Slugs or names, e.g. python~3.12 react.")
    install.add_argument(
        "--index-only",
        action="store_true",
        help="Only fetch the entry index; pages are then fetched when read.",
    )
    install.add_argument("--force", action="store_true", help="Re-download even if current.")

    remove = sub.add_parser("remove", parents=[common], help="Delete installed docs.")
    remove.add_argument("docs", nargs="+")

    update = sub.add_parser("update", parents=[common], help="Update docs DevDocs has rebuilt.")
    update.add_argument("--check", action="store_true", help="Only list what is out of date.")

    search = sub.add_parser("search", parents=[common], help="Search entry names.")
    search.add_argument("query")
    search.add_argument("-d", "--doc", action="append", dest="docs", help="Doc to search.")
    search.add_argument("-n", "--limit", type=int, default=20)

    fulltext = sub.add_parser("fulltext", parents=[common], help="Full-text search page contents.")
    fulltext.add_argument("query")
    fulltext.add_argument("-d", "--doc", action="append", dest="docs", help="Doc to search.")
    fulltext.add_argument("-n", "--limit", type=int, default=10)

    read = sub.add_parser("read", parents=[common], help="Print a page as Markdown.")
    read.add_argument("doc")
    read.add_argument("path")
    read.add_argument("--full-page", action="store_true")

    entries = sub.add_parser("entries", parents=[common], help="Browse a doc's sections.")
    entries.add_argument("doc")
    entries.add_argument("type", nargs="?", help="A section to list.")

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not any(arg in COMMANDS or arg in {"-h", "--help", "--version"} for arg in argv):
        argv.insert(0, "serve")
    args = build_parser().parse_args(argv)

    # stdout carries the protocol under stdio, so logs go to stderr.
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    settings = Settings.from_env()
    if getattr(args, "data_dir", None):
        settings.data_dir = args.data_dir.expanduser()
    store = DevDocsStore(settings)

    try:
        if args.command == "serve":
            return _serve(store, args)
        return _run_command(store, args)
    except DevDocsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        store.close()


def _serve(store: DevDocsStore, args: argparse.Namespace) -> int:
    from .server import create_server

    server = create_server(store)
    log.info("Docs are stored in %s", store.db_path)

    preload = _split_docs(args.preload)
    if preload:
        # In the background, so clients are not kept waiting on big downloads.
        threading.Thread(target=_preload, args=(store, preload), daemon=True).start()

    if args.transport == "stdio":
        server.run("stdio")
    elif args.transport == "sse":
        server.run("sse", host=args.host, port=args.port)
    else:
        log.info("Serving MCP at http://%s:%d/mcp", args.host, args.port)
        server.run("streamable-http", host=args.host, port=args.port)
    return 0


def _preload(store: DevDocsStore, docs: list[str]) -> None:
    for name in docs:
        try:
            result = store.install(name)
            log.info("Preload %s: %s", result.slug, result.status)
        except DevDocsError as error:
            log.error("Preload %s failed: %s", name, error)


def _run_command(store: DevDocsStore, args: argparse.Namespace) -> int:
    command = args.command
    if command == "list":
        return _call(store, "list_docs", {"installed_only": True})
    if command == "catalog":
        if args.refresh:
            store.catalog(refresh=True)
        return _call(store, "list_docs", {"query": args.query, "limit": 1000})
    if command == "install":
        failed = 0
        for name in args.docs:
            print(f"Installing {name}...", file=sys.stderr, flush=True)
            try:
                result = store.install(name, full=not args.index_only, force=args.force)
            except DevDocsError as error:
                print(f"  {name}: {error}", file=sys.stderr)
                failed += 1
                continue
            doc = result.doc
            print(f"  {result.slug}: {result.status} ({doc.entries} entries, {doc.pages} pages)")
        return 1 if failed else 0
    if command == "remove":
        for name in args.docs:
            print(f"{store.remove(name)}: removed")
        return 0
    if command == "update":
        store.catalog(refresh=True)
        outdated = store.outdated()
        if not outdated:
            print("All installed docs are up to date.")
            return 0
        for doc, _ in outdated:
            if args.check:
                print(f"{doc.slug}: update available")
            else:
                result = store.install(doc.slug, full=doc.offline, force=True)
                print(f"{result.slug}: {result.status}")
        return 0
    if command == "search":
        return _call(
            store, "search_docs", {"query": args.query, "docs": args.docs, "limit": args.limit}
        )
    if command == "fulltext":
        return _call(
            store, "search_content", {"query": args.query, "docs": args.docs, "limit": args.limit}
        )
    if command == "read":
        return _call(
            store,
            "read_page",
            {
                "doc": args.doc,
                "path": args.path,
                "full_page": args.full_page,
                "max_length": 200_000,
            },
        )
    if command == "entries":
        return _call(store, "list_entries", {"doc": args.doc, "type": args.type, "limit": 1000})
    raise AssertionError(command)


def _call(store: DevDocsStore, tool: str, arguments: dict[str, Any]) -> int:
    """Run an MCP tool in-process and print what a model would see."""
    from mcp.server.mcpserver.exceptions import ToolError

    from .server import create_server

    server = create_server(store)
    try:
        result = anyio.run(server.call_tool, tool, arguments)
    except ToolError as error:
        message = re.sub(r"^Error executing tool \w+: ", "", str(error))
        print(f"error: {message}", file=sys.stderr)
        return 1
    text = "\n".join(block.text for block in result.content if hasattr(block, "text"))
    print(text)
    return 1 if result.is_error else 0
