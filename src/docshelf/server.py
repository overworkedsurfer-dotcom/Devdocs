"""The MCP server: tools for searching and reading the knowledge folder."""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .api import add_api_routes
from .crawler import CrawlManager, CrawlOptions
from .knowledge import KnowledgeBase, NotFoundError

INSTRUCTIONS = """\
The user's knowledge folder: their own notes and documents (Markdown, text, \
HTML, PDF), plus web pages saved from their browser or crawled, as Markdown.

- search finds sections by their words. Results are paths like \
notes/deploy.md#rolling-back.
- read returns a file, or one section of it (path#anchor). A saved web page \
can also be read by its original URL.
- list_files browses the folders.
- crawl_site saves a website into the folder in the background; crawl_status \
reports progress.

Answers come from the folder only; nothing is looked up online except by crawl_site.
"""

_READS = ToolAnnotations(read_only_hint=True, open_world_hint=False)

Folder = Annotated[
    str | None,
    Field(description="Only look in this folder, e.g. 'notes' or 'web/react.dev'."),
]


def create_server(
    knowledge: KnowledgeBase | None = None, crawls: CrawlManager | None = None
) -> MCPServer:
    knowledge = knowledge or KnowledgeBase()
    crawls = crawls or CrawlManager(knowledge)
    server = MCPServer("docshelf", title="Docshelf", instructions=INSTRUCTIONS, version=__version__)

    @server.tool(annotations=_READS, structured_output=False)
    def search(
        query: Annotated[str, Field(description="Words to look for.")],
        folder: Folder = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> str:
        """Search the knowledge folder, best matches first.

        Finds sections of files: headings and titles count most. Read a
        result with read(path), using the path exactly as given.
        """
        try:
            hits = knowledge.search(query, folder, limit)
        except ValueError as error:
            raise ToolError(str(error)) from error
        where = f" in {folder}" if folder else ""
        if not hits:
            return f'Nothing matches "{query}"{where}. Try other words, or list_files to browse.'
        lines = [f'{_count(len(hits), "result")} for "{query}"{where}:']
        for hit in hits:
            target = f"{hit.path}#{hit.anchor}" if hit.anchor else hit.path
            heading = f" › {hit.heading}" if hit.heading and hit.heading != hit.title else ""
            snippet = " ".join(hit.snippet.split())
            lines.append(f"- {target} — {hit.title}{heading}\n  {snippet}")
        return "\n".join(lines)

    @server.tool(annotations=_READS, structured_output=False)
    def read(
        path: Annotated[
            str,
            Field(
                description="A path from search or list_files, e.g. 'notes/deploy.md', with "
                "#anchor for one section. The URL of a saved web page also works."
            ),
        ],
        offset: Annotated[int, Field(ge=0, description="Character offset to continue from.")] = 0,
        max_length: Annotated[int, Field(ge=1000, le=200_000)] = 20_000,
    ) -> str:
        """Read a file from the knowledge folder as Markdown.

        With a #anchor, only that section (and its subsections) is returned.
        Long files come in chunks: follow the offset given at the end.
        """
        try:
            reading = knowledge.read(path)
        except NotFoundError as error:
            raise ToolError(str(error)) from error

        header = f"path: {reading.path}"
        if reading.source:
            header += f" | source: {reading.source}"
        notes = []
        if reading.anchor and reading.anchor_found:
            notes.append(
                f"(Only the #{reading.anchor} section; read {reading.path} for all of it.)"
            )
        elif reading.anchor:
            notes.append(f"(No #{reading.anchor} section in this file, so here is all of it.)")

        text = reading.text
        chunk, next_offset = _chunk(text, offset, max_length)
        footer = ""
        if next_offset is not None:
            footer = (
                f"\n\n(Characters {offset}-{next_offset} of {len(text)}. "
                f"Call read again with offset={next_offset} for the rest.)"
            )
        elif offset and not chunk:
            chunk = f"(offset {offset} is past the end; the text has {len(text)} characters)"
        return "\n".join([header, *notes]) + "\n\n---\n\n" + chunk + footer

    @server.tool(annotations=_READS, structured_output=False)
    def list_files(
        folder: Annotated[
            str, Field(description="Folder to list, e.g. 'web'. Empty for the top level.")
        ] = "",
    ) -> str:
        """List a folder of the knowledge folder: subfolders with file counts,
        then files with their titles."""
        folders, files = knowledge.listing(folder)
        name = folder.strip("/") or "(top level)"
        if not folders and not files:
            if folder.strip("/"):
                raise ToolError(f"No folder {folder!r}, or it has no readable files.")
            return "The knowledge folder is empty."
        lines = [f"{name}:"]
        lines += [f"- {sub}/ ({_count(n, 'file')})" for sub, n in sorted(folders.items())]
        lines += [f"- {path} — {title}" for path, title in files]
        return "\n".join(lines)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=True
        ),
        structured_output=False,
    )
    def crawl_site(
        url: Annotated[str, Field(description="The page to start from.")],
        max_pages: Annotated[int, Field(ge=1, le=5000)] = 100,
        prefix: Annotated[
            str | None,
            Field(
                description="Only follow links starting with this URL. Defaults to the start "
                "page's folder, e.g. https://site/docs/ for https://site/docs/intro."
            ),
        ] = None,
    ) -> str:
        """Save a website, page by page, into the knowledge folder.

        Runs in the background; crawl_status reports progress. Pages are
        saved under web/<site>/ and are searchable as soon as each is saved.
        """
        try:
            job = crawls.start(CrawlOptions(url=url, max_pages=max_pages, prefix=prefix))
        except ValueError as error:
            raise ToolError(str(error)) from error
        return (
            f"Crawl {job.id} started: up to {max_pages} pages under {job.options.scope()}. "
            "Check on it with crawl_status."
        )

    @server.tool(annotations=_READS, structured_output=False)
    def crawl_status(job_id: str | None = None) -> str:
        """Progress of crawls started since the server started."""
        jobs = [crawls.get(job_id)] if job_id else crawls.jobs()
        jobs = [job for job in jobs if job is not None]
        if not jobs:
            return f"No crawl {job_id}." if job_id else "No crawls have run."
        return "\n".join(f"- {job.summary()}" for job in jobs)

    add_api_routes(server, knowledge, crawls)
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
    return f"{n} {noun}{'' if n == 1 else 's'}"
