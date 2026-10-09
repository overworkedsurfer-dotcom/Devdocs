import time

import httpx2
import pytest

from docshelf.crawler import CrawlManager, CrawlOptions, crawl


def page(title, *links):
    anchors = "".join(f'<a href="{link}">{link}</a>' for link in links)
    return (
        f"<html><head><title>{title}</title></head><body><nav>{anchors}</nav>"
        f"<main><h1>{title}</h1><p>{title} explained in enough words to be worth saving, "
        f"with details and examples for readers.</p></main></body></html>"
    )


SITE = {
    "/docs/": page(
        "Home", "intro", "guide/setup", "/blog/post", "https://other.site/docs/x", "logo.png"
    ),
    "/docs/intro": page("Intro", "/docs/", "guide/setup?tab=2", "private/keys"),
    "/docs/guide/setup": page("Setup", "../intro", "advanced"),
    "/docs/guide/advanced": page("Advanced", "/docs/moved", "/docs/elsewhere"),
    "/docs/private/keys": page("Keys"),
    "/blog/post": page("Blog"),
}


class FakeSite:
    def __init__(self, robots="User-agent: *\nDisallow: /docs/private/\n"):
        self.requests = []
        self.robots = robots

    def __call__(self, request):
        path = request.url.path
        self.requests.append((request.url.host, path, request.headers.get("cookie")))
        if path == "/robots.txt":
            return httpx2.Response(200, text=self.robots)
        if path == "/docs/moved":
            return httpx2.Response(301, headers={"location": "https://site.test/blog/post"})
        if path == "/docs/elsewhere":
            return httpx2.Response(302, headers={"location": "https://other.test/page"})
        if path in SITE:
            return httpx2.Response(200, text=SITE[path], headers={"content-type": "text/html"})
        return httpx2.Response(404)

    def client(self):
        return httpx2.Client(transport=httpx2.MockTransport(self), follow_redirects=True)


def test_scope():
    assert CrawlOptions("https://site.test/docs/intro").scope() == "https://site.test/docs/"
    assert CrawlOptions("https://site.test/docs/").scope() == "https://site.test/docs/"
    assert CrawlOptions("https://site.test/a", prefix="https://site.test/").scope() == (
        "https://site.test/"
    )


def test_crawl_stays_in_scope(knowledge):
    site = FakeSite()
    options = CrawlOptions("https://site.test/docs/", headers={"Cookie": "session=1"})
    job = crawl(knowledge, options, client=site.client())
    assert job.status == "done"
    saved = sorted(path for path, _ in knowledge.listing("web/site.test/docs")[1])
    assert saved == ["web/site.test/docs/index.md", "web/site.test/docs/intro.md"]
    assert [p for p, _ in knowledge.listing("web/site.test/docs/guide")[1]] == [
        "web/site.test/docs/guide/advanced.md",
        "web/site.test/docs/guide/setup.md",
    ]
    fetched = [path for _, path, _ in site.requests]
    assert "/docs/private/keys" not in fetched  # robots.txt
    assert "/docs/logo.png" not in fetched
    assert "/blog/post" in fetched  # reached through a redirect, then dropped
    # One redirect leaves the scope; the other leads off-site to a 404.
    assert (job.saved, job.skipped, job.failed) == (4, 1, 1)
    # The cookie goes to every request on the site, redirects included, and
    # never to another site.
    for host, path, cookie in site.requests:
        assert cookie == ("session=1" if host == "site.test" else None), (host, path)
    assert ("other.test", "/page", None) in site.requests

    again = crawl(knowledge, options, client=site.client())
    assert (again.saved, again.unchanged) == (0, 4)


def test_crawl_limits(knowledge):
    job = crawl(
        knowledge, CrawlOptions("https://site.test/docs/", max_pages=2), client=FakeSite().client()
    )
    assert job.pages == 2

    site = FakeSite(robots="")
    job = crawl(
        knowledge,
        CrawlOptions("https://site.test/docs/", respect_robots=False, exclude="guide"),
        client=site.client(),
    )
    fetched = [path for _, path, _ in site.requests]
    assert "/docs/private/keys" in fetched
    assert not any("guide" in path for path in fetched)


def test_manager_runs_crawls_in_the_background(knowledge):
    manager = CrawlManager(knowledge, client_factory=FakeSite().client)
    job = manager.start(CrawlOptions("https://site.test/docs/"))
    deadline = time.time() + 10
    while job.status in ("queued", "running") and time.time() < deadline:
        time.sleep(0.05)
    assert job.status == "done"
    assert manager.get(job.id) is job
    assert job.as_dict()["saved"] == 4
    with pytest.raises(ValueError):
        manager.start(CrawlOptions("ftp://site.test/"))
