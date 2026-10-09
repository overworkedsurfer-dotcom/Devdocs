"""Command line: run the MCP server, or search and manage the knowledge folder.

docshelf                          serve over stdio (what MCP clients launch)
docshelf serve --transport http   serve over HTTP at :8000/mcp, plus the extension's API
docshelf token                    print the token the browser extension needs
docshelf search "rolling back"    search like the MCP tool does
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

import anyio

from . import __version__
from .config import Settings
from .knowledge import KnowledgeBase

log = logging.getLogger("docshelf")

COMMANDS = {"serve", "token", "search", "read", "ls", "crawl", "reindex"}


def build_parser() -> argparse.ArgumentParser:
    # Accepted before or after the command. SUPPRESS keeps a subcommand's
    # defaults from overwriting a value given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="The knowledge folder (env DOCSHELF_DIR; default ~/knowledge).",
    )
    common.add_argument(
        "--index-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Where the search index lives (env DOCSHELF_INDEX_DIR; default <dir>/.docshelf).",
    )
    common.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="Log more."
    )

    parser = argparse.ArgumentParser(
        prog="docshelf",
        description="Your knowledge folder, searchable by AI assistants over MCP.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    serve = sub.add_parser("serve", parents=[common], help="Run the MCP server (the default).")
    serve.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http", "sse"],
        default=os.environ.get("DOCSHELF_TRANSPORT") or "stdio",
        help="stdio for clients that launch the server; http to serve on --host:--port "
        "(env DOCSHELF_TRANSPORT; default stdio).",
    )
    serve.add_argument(
        "--host",
        default=os.environ.get("DOCSHELF_HOST") or "127.0.0.1",
        help="HTTP bind address; 0.0.0.0 to accept other machines (env DOCSHELF_HOST).",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DOCSHELF_PORT") or 8000),
        help="HTTP port (env DOCSHELF_PORT; default 8000).",
    )

    sub.add_parser("token", parents=[common], help="Print the API token for the extension.")

    search = sub.add_parser("search", parents=[common], help="Search the folder.")
    search.add_argument("query")
    search.add_argument("-f", "--folder", help="Only search this folder.")
    search.add_argument("-n", "--limit", type=int, default=10)

    read = sub.add_parser("read", parents=[common], help="Print a file (path#section or URL).")
    read.add_argument("path")

    ls = sub.add_parser("ls", parents=[common], help="List a folder.")
    ls.add_argument("folder", nargs="?", default="")

    crawl = sub.add_parser("crawl", parents=[common], help="Save a website into the folder.")
    crawl.add_argument("url")
    crawl.add_argument("--max-pages", type=int, default=200)
    crawl.add_argument("--prefix", help="Only follow links under this URL.")
    crawl.add_argument("--include", help="Only crawl URLs matching this regex.")
    crawl.add_argument("--exclude", help="Skip URLs matching this regex.")
    crawl.add_argument("--selector", help="CSS selector of the main content.")
    crawl.add_argument("--delay", type=float, default=0.0, help="Seconds between requests.")
    crawl.add_argument(
        "-H",
        "--header",
        action="append",
        default=[],
        help="Extra request header, e.g. 'Cookie: session=...' for sites behind a login.",
    )
    crawl.add_argument("--ignore-robots", action="store_true", help="Ignore robots.txt.")

    sub.add_parser("reindex", parents=[common], help="Re-read every file into the index.")
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

    settings = Settings.from_env(getattr(args, "dir", None), getattr(args, "index_dir", None))
    if args.command == "token":
        print(settings.api_token())
        return 0

    knowledge = KnowledgeBase(settings)
    try:
        if args.command == "serve":
            return _serve(knowledge, args)
        return _run(knowledge, args)
    except KeyboardInterrupt:
        return 130
    finally:
        knowledge.close()


def _serve(knowledge: KnowledgeBase, args: argparse.Namespace) -> int:
    from .server import create_server

    server = create_server(knowledge)
    log.info("Knowledge folder: %s", knowledge.root)
    # Index in the background so clients are not kept waiting on a big folder.
    threading.Thread(target=knowledge.index.sync, daemon=True).start()

    if args.transport == "stdio":
        server.run("stdio")
    elif args.transport == "sse":
        server.run("sse", host=args.host, port=args.port)
    else:
        log.info("MCP at http://%s:%d/mcp; extension API at /api", args.host, args.port)
        log.info("The extension's token: run `docshelf token`")
        server.run("streamable-http", host=args.host, port=args.port)
    return 0


def _run(knowledge: KnowledgeBase, args: argparse.Namespace) -> int:
    if args.command == "search":
        return _call(
            knowledge, "search", {"query": args.query, "folder": args.folder, "limit": args.limit}
        )
    if args.command == "read":
        return _call(knowledge, "read", {"path": args.path, "max_length": 200_000})
    if args.command == "ls":
        return _call(knowledge, "list_files", {"folder": args.folder})
    if args.command == "reindex":
        result = knowledge.index.sync(force=True)
        print(
            f"{knowledge.index.count()} files indexed "
            f"({len(result.added)} new, {len(result.updated)} changed, "
            f"{len(result.removed)} removed, {len(result.failed)} unreadable)"
        )
        for path in result.failed:
            print(f"  unreadable: {path}", file=sys.stderr)
        return 0
    if args.command == "crawl":
        return _crawl(knowledge, args)
    raise AssertionError(args.command)


def _crawl(knowledge: KnowledgeBase, args: argparse.Namespace) -> int:
    from .crawler import CrawlJob, CrawlOptions, crawl

    headers = {}
    for header in args.header:
        name, sep, value = header.partition(":")
        if not sep:
            print(f"error: header must look like 'Name: value', not {header!r}", file=sys.stderr)
            return 2
        headers[name.strip()] = value.strip()
    options = CrawlOptions(
        url=args.url,
        max_pages=args.max_pages,
        prefix=args.prefix,
        include=args.include,
        exclude=args.exclude,
        selector=args.selector,
        delay=args.delay,
        headers=headers,
        respect_robots=not args.ignore_robots,
    )
    try:
        scope = options.scope()
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Crawling {scope} (up to {args.max_pages} pages)...", file=sys.stderr)
    job = CrawlJob("cli", options)

    def report() -> None:
        while job.status in ("queued", "running"):
            if job.current:
                print(f"  {job.pages:>4} saved  {job.current}", file=sys.stderr, flush=True)
            threading.Event().wait(2)

    threading.Thread(target=report, daemon=True).start()
    crawl(knowledge, options, job)
    print(job.summary().replace("cli ", "", 1))
    return 0 if job.status == "done" else 1


def _call(knowledge: KnowledgeBase, tool: str, arguments: dict[str, Any]) -> int:
    """Run an MCP tool in-process and print what a model would see."""
    import re

    from mcp.server.mcpserver.exceptions import ToolError

    from .server import create_server

    server = create_server(knowledge)
    try:
        result = anyio.run(server.call_tool, tool, arguments)
    except ToolError as error:
        message = re.sub(r"^Error executing tool \w+: ", "", str(error))
        print(f"error: {message}", file=sys.stderr)
        return 1
    print("\n".join(block.text for block in result.content if hasattr(block, "text")))
    return 1 if result.is_error else 0
