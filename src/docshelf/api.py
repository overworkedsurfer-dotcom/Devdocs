"""HTTP API for the browser extension, served next to /mcp in HTTP mode.

Every /api route needs the token (`Authorization: Bearer <token>`); see
`docshelf token`. There are no CORS headers: the extension calls from its
own pages, which Chrome lets through for hosts it has permission for, and
ordinary websites cannot.
"""

from __future__ import annotations

import functools
import hmac
import json
from collections.abc import Awaitable, Callable

import anyio
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import __version__
from .crawler import CrawlManager, CrawlOptions
from .knowledge import KnowledgeBase

MAX_BODY_BYTES = 25 * 1024 * 1024

Handler = Callable[[Request], Awaitable[Response]]


def add_api_routes(server: MCPServer, knowledge: KnowledgeBase, crawls: CrawlManager) -> None:
    token = knowledge.settings.api_token()

    def guarded(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapper(request: Request) -> Response:
            header = request.headers.get("authorization", "")
            supplied = header[7:].strip() if header[:7].lower() == "bearer " else ""
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                return _error(401, "Missing or wrong token. `docshelf token` prints it.")
            try:
                return await handler(request)
            except ValueError as error:
                return _error(400, str(error))

        return wrapper

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    @server.custom_route("/api/status", methods=["GET"])
    @guarded
    async def status(request: Request) -> Response:
        files = await anyio.to_thread.run_sync(knowledge.count)
        return JSONResponse(
            {"version": __version__, "files": files, "web_folder": knowledge.settings.web_folder}
        )

    @server.custom_route("/api/pages", methods=["POST"])
    @guarded
    async def save_page(request: Request) -> Response:
        """Save a page: {"url": ..., "html": ..., "title": optional, "selector": optional}."""
        body = await _json(request)
        url, html = body.get("url"), body.get("html")
        if not isinstance(url, str) or not isinstance(html, str):
            raise ValueError('Send {"url": "...", "html": "..."}.')
        saved = await anyio.to_thread.run_sync(
            functools.partial(
                knowledge.save_page,
                url,
                html,
                title=_optional_str(body, "title"),
                selector=_optional_str(body, "selector"),
            )
        )
        return JSONResponse(
            {"status": saved.status, "path": saved.path, "title": saved.title, "url": saved.url}
        )

    @server.custom_route("/api/crawl", methods=["GET", "POST"])
    @guarded
    async def crawl(request: Request) -> Response:
        if request.method == "GET":
            return JSONResponse({"jobs": [job.as_dict() for job in crawls.jobs()]})
        body = await _json(request)
        if not isinstance(body.get("url"), str):
            raise ValueError('Send {"url": "...", "max_pages": optional, "prefix": optional}.')
        headers = body.get("headers") or {}
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
        ):
            raise ValueError("headers must map header names to strings.")
        options = CrawlOptions(
            url=body["url"],
            max_pages=int(body.get("max_pages") or 200),
            prefix=_optional_str(body, "prefix"),
            include=_optional_str(body, "include"),
            exclude=_optional_str(body, "exclude"),
            selector=_optional_str(body, "selector"),
            headers=headers,
        )
        job = crawls.start(options)
        return JSONResponse(job.as_dict(), status_code=202)

    @server.custom_route("/api/crawl/{job_id}", methods=["GET", "DELETE"])
    @guarded
    async def crawl_job(request: Request) -> Response:
        job_id = request.path_params["job_id"]
        job = crawls.cancel(job_id) if request.method == "DELETE" else crawls.get(job_id)
        if job is None:
            return _error(404, f"No crawl {job_id}.")
        return JSONResponse(job.as_dict())


async def _json(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError(f"Request body over {MAX_BODY_BYTES // 1024 // 1024} MB.")
    try:
        body = json.loads(raw)
    except ValueError as error:
        raise ValueError("The request body is not JSON.") from error
    if not isinstance(body, dict):
        raise ValueError("The request body must be a JSON object.")
    return body


def _optional_str(body: dict, key: str) -> str | None:
    value = body.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    return value or None


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)
