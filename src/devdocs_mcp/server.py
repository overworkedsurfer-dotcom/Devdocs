"""The MCP server: DevDocs search and reading tools for language models."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .render import html_to_markdown
from .store import DevDocsError, DevDocsStore, InstalledDoc

INSTRUCTIONS = """\
Offline API documentation for hundreds of languages, frameworks and libraries: \
the docs from devdocs.io, stored locally.

1. search_docs finds API entries (functions, classes, methods, guides) by name, \
e.g. query="getcwd", docs=["python"]. Name docs by slug ("python~3.12") or plainly \
("python", "react", "js"); one that is not installed yet is fetched automatically.
2. read_page reads a result as Markdown: pass the result's doc and path.
3. search_content is full-text search, for when you do not know the API's name. \
It covers docs installed offline (install_docs) and pages already read.

list_docs shows what DevDocs offers and what is installed; list_entries browses \
a doc's table of contents.
"""

DocList = Annotated[
    list[str] | None,
    Field(
        description='Docs to search, by slug or name (e.g. ["python~3.12", "javascript"]). '
        "Omit to search every installed doc."
    ),
]

_READS = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)


def create_server(store: DevDocsStore | None = None) -> MCPServer:
    store = store or DevDocsStore()
    server = MCPServer("devdocs", title="DevDocs", instructions=INSTRUCTIONS, version=__version__)
    rendered: OrderedDict[tuple, tuple[str, bool]] = OrderedDict()
    rendered_lock = threading.Lock()

    @server.tool(annotations=_READS, structured_output=False)
    def search_docs(
        query: Annotated[
            str, Field(description="An API name or part of one, e.g. 'os.path.join' or 'useState'.")
        ],
        docs: DocList = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> str:
        """Search documentation entries by name, ranked the way devdocs.io ranks them.

        Matches function, class, method, property and guide names, exactly or
        fuzzily. Read a result with read_page(doc, path).
        """
        try:
            slugs = store.scope(docs)
            results = store.search(query, slugs, limit)
        except DevDocsError as error:
            raise ToolError(str(error)) from error
        scope = ", ".join(slugs)
        if not results:
            return (
                f'No entries named like "{query}" in {scope}. Try a shorter name, another doc, '
                "or search_content for full-text search."
            )
        lines = [f'{_count(len(results), "entry")} matching "{query}" (searched {scope}):']
        lines += [f"- {e.name} [{e.type}] doc={e.doc} path={e.path}" for e, _ in results]
        return "\n".join(lines)

    @server.tool(annotations=_READS, structured_output=False)
    def read_page(
        doc: Annotated[str, Field(description="The doc slug or name, e.g. 'python~3.12'.")],
        path: Annotated[
            str,
            Field(
                description="The entry path from search_docs or list_entries, e.g. "
                "'library/os#os.getcwd'. A devdocs.io URL also works."
            ),
        ],
        full_page: Annotated[
            bool,
            Field(description="Return the whole page even when the path has a #fragment."),
        ] = False,
        offset: Annotated[int, Field(ge=0, description="Character offset to continue from.")] = 0,
        max_length: Annotated[int, Field(ge=1000, le=200_000)] = 20_000,
    ) -> str:
        """Read a documentation page as Markdown.

        When the path has a #fragment, only the section documenting that entry
        is returned, unless full_page is true. Relative links in the Markdown
        are paths in the same doc that read_page accepts. Long pages come in
        chunks: follow the offset given at the end.
        """
        try:
            page = store.page(doc, path)
        except DevDocsError as error:
            raise ToolError(str(error)) from error

        fragment = "" if full_page else page.fragment
        # Paging through a long page re-reads it; convert it once.
        key = (page.doc, page.path, fragment, hash(page.html))
        with rendered_lock:
            cached = rendered.get(key)
            if cached is not None:
                rendered.move_to_end(key)
        if cached is None:
            cached = html_to_markdown(page.html, page.path, fragment or None)
            with rendered_lock:
                rendered[key] = cached
                if len(rendered) > 32:
                    rendered.popitem(last=False)
        markdown, found = cached

        location = f"{page.path}#{page.fragment}" if page.fragment else page.path
        header = [f"doc: {page.doc} | path: {location} | https://devdocs.io/{page.doc}/{location}"]
        if fragment and found:
            header.append(
                f"(Only the #{fragment} section; pass full_page=true for the whole page.)"
            )
        elif fragment:
            header.append(f"(No #{fragment} anchor on this page, so here is the whole page.)")

        chunk, next_offset = _chunk(markdown, offset, max_length)
        footer = ""
        if next_offset is not None:
            footer = (
                f"\n\n(Characters {offset}-{next_offset} of {len(markdown)}. "
                f"Call read_page again with offset={next_offset} for the rest.)"
            )
        elif offset and not chunk:
            chunk = f"(offset {offset} is past the end; the page has {len(markdown)} characters)"
        return "\n".join(header) + "\n\n---\n\n" + chunk + footer

    @server.tool(annotations=_READS, structured_output=False)
    def search_content(
        query: Annotated[str, Field(description="Words to look for in page text.")],
        docs: DocList = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> str:
        """Full-text search inside documentation pages, best matches first.

        Use it when you do not know an API's name. It covers docs installed
        offline (install_docs with full=True) and pages read before; other
        docs are only searched by name (search_docs).
        """
        try:
            slugs = store.scope(docs)
            hits = store.search_content(query, slugs, limit)
        except DevDocsError as error:
            raise ToolError(str(error)) from error

        lines = []
        if hits:
            lines.append(f'{_count(len(hits), "page")} matching "{query}":')
            for hit in hits:
                lines.append(f"- {hit.title} — doc={hit.doc} path={hit.path}\n  {hit.snippet}")
        else:
            lines.append(f'No page text matches "{query}" in {", ".join(slugs)}.')
        partial = [doc.slug for doc in map(store.installed_doc, slugs) if doc and not doc.offline]
        if partial:
            verb = "is" if len(partial) == 1 else "are"
            lines.append(
                f"\nNote: {', '.join(partial)} {verb} not installed offline, so only pages "
                "read before were searched. install_docs downloads every page."
            )
        return "\n".join(lines)

    @server.tool(annotations=_READS, structured_output=False)
    def list_entries(
        doc: Annotated[str, Field(description="The doc slug or name, e.g. 'python~3.12'.")],
        type: Annotated[
            str | None,
            Field(description="A section name from the listing without it, e.g. 'os.path'."),
        ] = None,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
    ) -> str:
        """Browse a doc's table of contents, like the devdocs.io sidebar.

        Without `type`, lists the doc's sections and how many entries each
        has. With `type`, lists that section's entries and their paths.
        """
        try:
            if type is None:
                slug, types = store.types(doc)
                if types:
                    shown = types[offset : offset + limit]
                    lines = [f"{slug} has {len(types)} sections (pass one as type):"]
                    lines += [f"- {t.name} ({t.count})" for t in shown]
                    return "\n".join(lines) + _more(offset, len(shown), len(types))
            slug, entries = store.entries(doc, type)
        except DevDocsError as error:
            raise ToolError(str(error)) from error
        if not entries:
            raise ToolError(
                f"{slug} has no section named {type!r}. Call without type to list them."
            )
        shown = entries[offset : offset + limit]
        title = f"section {type!r}" if type else "entries"
        lines = [f"{slug} {title}: {_count(len(entries), 'entry')}"]
        lines += [f"- {e.name} path={e.path}" for e in shown]
        return "\n".join(lines) + _more(offset, len(shown), len(entries))

    @server.tool(annotations=_READS, structured_output=False)
    def list_docs(
        query: Annotated[
            str,
            Field(
                description="Filter by name, slug or alias, e.g. 'python' or 'react'. "
                "Empty: installed docs plus the names of everything available."
            ),
        ] = "",
        installed_only: bool = False,
        limit: Annotated[int, Field(ge=1, le=1000)] = 50,
    ) -> str:
        """List the documentation sets DevDocs offers and which are installed here."""
        installed = {doc.slug: doc for doc in store.installed()}
        try:
            catalog = [] if installed_only else store.catalog()
            catalog_error = None
        except DevDocsError as error:
            catalog, catalog_error = [], str(error)

        needle = query.strip().lower()
        if needle:
            matches = [
                doc
                for doc in (catalog or [_as_catalog(doc) for doc in installed.values()])
                if needle in doc["slug"].lower()
                or needle in (doc.get("name") or "").lower()
                or needle == (doc.get("alias") or "").lower()
            ]
            if installed_only:
                matches = [doc for doc in matches if doc["slug"] in installed]
            if not matches:
                return f'No docs match "{query}".' + (
                    f" ({catalog_error})" if catalog_error else ""
                )
            lines = [f'{len(matches)} docs match "{query}":']
            for doc in matches[:limit]:
                lines.append(_describe(doc, installed.get(doc["slug"])))
            return "\n".join(lines) + _more(0, min(limit, len(matches)), len(matches))

        lines = [f"Installed ({len(installed)}):"]
        lines += [_describe(_as_catalog(doc), doc) for doc in installed.values()] or ["(none)"]
        if catalog:
            names = sorted({doc.get("name") or doc["slug"] for doc in catalog}, key=str.lower)
            lines.append(
                f"\nDevDocs offers {len(catalog)} docs ({len(names)} projects, many in several "
                "versions). Pass a query to see slugs and versions:"
            )
            lines.append(", ".join(names))
        elif catalog_error:
            lines.append(f"\nThe DevDocs catalog is unavailable: {catalog_error}")
        return "\n".join(lines)

    @server.tool(annotations=_WRITES, structured_output=False)
    def install_docs(
        docs: Annotated[
            list[str],
            Field(min_length=1, description='Docs by slug or name, e.g. ["python~3.12", "react"].'),
        ],
        full: Annotated[
            bool,
            Field(
                description="Download every page for offline reading and full-text search. "
                "False fetches only the entry index; pages are then fetched as they are read."
            ),
        ] = True,
    ) -> str:
        """Download docs, or update installed ones that DevDocs has rebuilt since."""
        lines, failures = [], 0
        for name in docs:
            try:
                result = store.install(name, full=full)
            except DevDocsError as error:
                failures += 1
                lines.append(f"- {name}: failed: {error}")
                continue
            lines.append(f"- {result.slug}: {result.status}, {_summary(result.doc)}")
        if failures == len(docs):
            raise ToolError("\n".join(lines))
        return "\n".join(lines)

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
        structured_output=False,
    )
    def remove_docs(
        docs: Annotated[list[str], Field(min_length=1, description="Installed docs to delete.")],
    ) -> str:
        """Delete installed docs and their cached pages from local storage."""
        lines = []
        for name in docs:
            try:
                lines.append(f"- {store.remove(name)}: removed")
            except DevDocsError as error:
                lines.append(f"- {name}: {error}")
        return "\n".join(lines)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    return server


def _chunk(text: str, offset: int, max_length: int) -> tuple[str, int | None]:
    """Slice `text` at `offset`, ending at a line break where one is near."""
    end = offset + max_length
    if end >= len(text):
        return text[offset:], None
    newline = text.rfind("\n", offset + max_length * 3 // 4, end)
    if newline > offset:
        end = newline + 1
    return text[offset:end], end


def _count(n: int, noun: str) -> str:
    plural = noun[:-1] + "ies" if noun.endswith("y") else noun + "s"
    return f"{n} {noun if n == 1 else plural}"


def _more(offset: int, shown: int, total: int) -> str:
    if offset + shown >= total:
        return ""
    end = offset + shown
    return f"\n(Showing {offset + 1}-{end} of {total}; pass offset={end} for more.)"


def _as_catalog(doc: InstalledDoc) -> dict:
    return {"slug": doc.slug, "name": doc.name, "version": doc.version, "release": doc.release}


def _describe(doc: dict, installed: InstalledDoc | None) -> str:
    title = f"{doc.get('name') or doc['slug']} {doc.get('version') or ''}".strip()
    release = f" (release {doc['release']})" if doc.get("release") else ""
    line = f"- {doc['slug']}: {title}{release}"
    if installed is not None:
        return f"{line} [installed: {_summary(installed)}]"
    if doc.get("db_size"):
        line += f", {_size(int(doc['db_size']))} offline"
    return line


def _summary(doc: InstalledDoc) -> str:
    entries = _count(doc.entries, "entry")
    if doc.offline:
        return f"{entries}, all {_count(doc.pages, 'page')} offline"
    return f"{entries}, {_count(doc.pages, 'page')} cached"


def _size(size: float) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
