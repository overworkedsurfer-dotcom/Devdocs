import time

import httpx2
import pytest
from conftest import TOKEN
from test_crawler import FakeSite

from docshelf.crawler import CrawlManager
from docshelf.server import create_server

pytestmark = pytest.mark.anyio

ARTICLE = (
    "<html><head><title>Notes</title></head><body><main><h1>Release notes</h1>"
    "<p>Version 2 adds offline sync, faster search and a new settings page.</p></main></body></html>"
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def api(knowledge):
    crawls = CrawlManager(knowledge, client_factory=FakeSite().client)
    app = create_server(knowledge, crawls).streamable_http_app()
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://docshelf.test") as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        yield client


async def test_token_is_required(api):
    assert (await api.get("/health")).json()["status"] == "ok"
    response = await api.get("/api/status", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert "token" in response.json()["error"]
    response = await api.get("/api/status", headers={"Authorization": ""})
    assert response.status_code == 401


async def test_save_pages(api, folder):
    url = "https://example.com/releases/2"
    response = await api.post("/api/pages", json={"url": url, "html": ARTICLE, "title": "Notes"})
    assert response.status_code == 200
    assert response.json() == {
        "status": "added",
        "path": "web/example.com/releases/2.md",
        "title": "Release notes",
        "url": url,
    }
    assert (folder / "web/example.com/releases/2.md").exists()
    again = await api.post("/api/pages", json={"url": url, "html": ARTICLE})
    assert again.json()["status"] == "unchanged"
    assert (await api.get("/api/status")).json()["files"] == 1


async def test_bad_requests(api):
    assert (await api.post("/api/pages", content=b"not json")).status_code == 400
    assert (await api.post("/api/pages", json={"url": 1})).status_code == 400
    response = await api.post("/api/pages", json={"url": "chrome://newtab", "html": ARTICLE})
    assert response.status_code == 400
    assert (await api.get("/api/crawl/nope")).status_code == 404


async def test_crawl(api):
    response = await api.post("/api/crawl", json={"url": "https://site.test/docs/", "max_pages": 3})
    assert response.status_code == 202
    job = response.json()
    deadline = time.time() + 10
    while job["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(0.05)
        job = (await api.get(f"/api/crawl/{job['id']}")).json()
    assert (job["status"], job["saved"]) == ("done", 3)
    assert [j["id"] for j in (await api.get("/api/crawl")).json()["jobs"]] == [job["id"]]
