"""Crawl a website into the knowledge folder."""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx2

from . import __version__
from .knowledge import KnowledgeBase
from .webpage import normalize_url

log = logging.getLogger(__name__)

USER_AGENT = f"docshelf/{__version__}"
_ASSET_RE = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|ico|css|js|mjs|map|json|xml|txt|pdf|zip|gz|tgz|tar|"
    r"mp4|webm|mp3|wav|woff2?|ttf|eot|exe|dmg|pkg|deb|rpm)$",
    re.IGNORECASE,
)


@dataclass
class CrawlOptions:
    url: str
    max_pages: int = 200
    # Only pages whose URL starts with this are crawled. Defaults to the
    # start page's folder: https://site/docs/intro -> https://site/docs/
    prefix: str | None = None
    include: str | None = None  # regex the URL must match
    exclude: str | None = None  # regex the URL must not match
    selector: str | None = None  # CSS selector of the content, if the guess is wrong
    delay: float = 0.0  # seconds between requests
    headers: dict[str, str] = field(default_factory=dict)  # e.g. Cookie, Authorization
    respect_robots: bool = True

    def scope(self) -> str:
        if self.prefix:
            return _strip(normalize_url(self.prefix))
        start = urlsplit(_strip(normalize_url(self.url)))
        folder = start.path if start.path.endswith("/") else start.path.rsplit("/", 1)[0] + "/"
        return urlunsplit((start.scheme, start.netloc, folder, "", ""))


@dataclass
class CrawlJob:
    id: str
    options: CrawlOptions
    status: str = "queued"  # running, done, failed or cancelled
    saved: int = 0  # added or updated
    unchanged: int = 0
    skipped: int = 0  # not HTML, or no readable text
    failed: int = 0
    queued: int = 0
    current: str = ""
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def pages(self) -> int:
        return self.saved + self.unchanged

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.options.url,
            "scope": self.options.scope(),
            "status": self.status,
            "saved": self.saved,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "failed": self.failed,
            "queued": self.queued,
            "current": self.current,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def summary(self) -> str:
        counts = f"{self.saved} saved, {self.unchanged} unchanged"
        if self.skipped:
            counts += f", {self.skipped} skipped"
        if self.failed:
            counts += f", {self.failed} failed"
        if self.status == "running":
            counts += f", {self.queued} queued"
        line = f"{self.id} {self.options.url}: {self.status}, {counts}"
        return f"{line} ({self.error})" if self.error else line


def crawl(
    knowledge: KnowledgeBase,
    options: CrawlOptions,
    job: CrawlJob | None = None,
    client: httpx2.Client | None = None,
) -> CrawlJob:
    """Fetch pages breadth-first from `options.url`, saving each into the
    knowledge folder, and following links that stay within the scope."""
    job = job or CrawlJob(uuid.uuid4().hex[:8], options)
    job.status, job.started_at = "running", time.time()
    own_client = client is None
    client = client or httpx2.Client(timeout=30, follow_redirects=True)
    headers = {"User-Agent": USER_AGENT, **options.headers}
    try:
        _crawl(knowledge, options, job, client, headers)
        job.status = "cancelled" if job.cancel.is_set() else "done"
    except Exception as error:
        log.exception("Crawl %s failed", job.id)
        job.status, job.error = "failed", str(error)
    finally:
        job.current, job.finished_at = "", time.time()
        if own_client:
            client.close()
    return job


def _crawl(
    knowledge: KnowledgeBase,
    options: CrawlOptions,
    job: CrawlJob,
    client: httpx2.Client,
    headers: dict[str, str],
) -> None:
    scope = options.scope()
    include = re.compile(options.include) if options.include else None
    exclude = re.compile(options.exclude) if options.exclude else None
    robots = _robots(client, scope, headers) if options.respect_robots else None

    def wanted(url: str) -> bool:
        return (
            url.startswith(scope)
            and not _ASSET_RE.search(urlsplit(url).path)
            and (include is None or include.search(url) is not None)
            and (exclude is None or exclude.search(url) is None)
            and (robots is None or robots.can_fetch(USER_AGENT, url))
        )

    start = _strip(normalize_url(options.url))
    queue = deque([start])
    seen = {start}
    while queue and job.pages + job.failed < options.max_pages and not job.cancel.is_set():
        url = queue.popleft()
        job.current, job.queued = url, len(queue)
        if url != start and not wanted(url):
            continue
        try:
            response, final = _fetch(client, url, headers)
        except httpx2.HTTPError as error:
            log.info("Crawl %s: %s failed: %s", job.id, url, error)
            job.failed += 1
            continue
        final = _strip(normalize_url(final))
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            job.failed += 1
        elif "html" not in content_type or (final != url and not wanted(final)):
            job.skipped += 1
        else:
            seen.add(final)
            saved = knowledge.save_page(
                final, response.text, selector=options.selector, via="crawl"
            )
            if saved.status == "skipped":
                job.skipped += 1
            elif saved.status == "unchanged":
                job.unchanged += 1
            else:
                job.saved += 1
            for link in saved.links:
                try:
                    link = _strip(normalize_url(link))
                except ValueError:
                    continue
                if link not in seen and wanted(link):
                    seen.add(link)
                    queue.append(link)
        job.queued = len(queue)
        if options.delay:
            time.sleep(options.delay)


def _fetch(client: httpx2.Client, url: str, headers: dict[str, str]) -> tuple[httpx2.Response, str]:
    """GET, following redirects by hand: httpx drops a Cookie header on
    redirects, and the user's credentials must only go to the site they
    were given for, never to another origin it redirects to."""
    origin = _origin(url)
    for _ in range(10):
        same_site = _origin(url) == origin
        hop_headers = headers if same_site else {"User-Agent": USER_AGENT}
        response = client.get(url, headers=hop_headers, follow_redirects=False)
        location = response.headers.get("location")
        if not (response.is_redirect and location):
            return response, url
        url = urljoin(url, location)
    raise httpx2.TooManyRedirects("Too many redirects", request=response.request)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _strip(url: str) -> str:
    """Crawl identity of a URL: without its query string."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _robots(client: httpx2.Client, scope: str, headers: dict[str, str]) -> RobotFileParser | None:
    parts = urlsplit(scope)
    url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    try:
        response = client.get(url, headers=headers)
    except httpx2.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser


class CrawlManager:
    """Runs crawls in background threads and remembers recent ones."""

    def __init__(
        self,
        knowledge: KnowledgeBase,
        client_factory: Callable[[], httpx2.Client] | None = None,
        keep: int = 20,
    ):
        self.knowledge = knowledge
        self.client_factory = client_factory
        self.keep = keep
        self._jobs: dict[str, CrawlJob] = {}
        self._lock = threading.Lock()

    def start(self, options: CrawlOptions) -> CrawlJob:
        """Validate the options and start crawling. Raises ValueError."""
        normalize_url(options.url)
        options.scope()
        for pattern in (options.include, options.exclude):
            if pattern:
                re.compile(pattern)
        if not 1 <= options.max_pages <= 100_000:
            raise ValueError("max_pages must be between 1 and 100000")
        job = CrawlJob(uuid.uuid4().hex[:8], options)
        with self._lock:
            self._jobs[job.id] = job
            finished = [j for j in self._jobs.values() if j.status not in ("queued", "running")]
            for old in finished[: max(0, len(self._jobs) - self.keep)]:
                del self._jobs[old.id]
        threading.Thread(target=self._run, args=(options, job), daemon=True).start()
        return job

    def _run(self, options: CrawlOptions, job: CrawlJob) -> None:
        client = self.client_factory() if self.client_factory else None
        try:
            crawl(self.knowledge, options, job, client)
        finally:
            if client is not None:
                client.close()

    def get(self, job_id: str) -> CrawlJob | None:
        return self._jobs.get(job_id)

    def jobs(self) -> list[CrawlJob]:
        with self._lock:
            return list(self._jobs.values())

    def cancel(self, job_id: str) -> CrawlJob | None:
        job = self._jobs.get(job_id)
        if job is not None:
            job.cancel.set()
        return job
