"""The local DevDocs store.

Everything lives in one SQLite file: the DevDocs catalog (docs.json), the
entry index of each installed doc, and page HTML, either downloaded in bulk
(an "offline" install, which also feeds the full-text index) or fetched one
page at a time on first read and cached.

Data comes from the same endpoints the DevDocs web app uses:

    https://devdocs.io/docs.json                       the catalog
    https://documents.devdocs.io/<slug>/index.json     entries and types
    https://documents.devdocs.io/<slug>/db.json        every page, keyed by path
    https://documents.devdocs.io/<slug>/<path>.html    a single page
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx2

from . import __version__
from .render import html_to_text, page_title
from .search import normalize_string
from .search import search as rank_entries

log = logging.getLogger(__name__)

DEFAULT_MANIFEST_URL = "https://devdocs.io/docs.json"
DEFAULT_DOCUMENTS_URL = "https://documents.devdocs.io"


class DevDocsError(Exception):
    """A failure whose message is meant for the user."""


class DocNotFoundError(DevDocsError):
    pass


class PageNotFoundError(DevDocsError):
    pass


def default_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "devdocs-mcp"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    manifest_url: str = DEFAULT_MANIFEST_URL
    documents_url: str = DEFAULT_DOCUMENTS_URL
    # Fetch a doc's index on first use instead of failing.
    auto_install: bool = True
    # How long a downloaded catalog is trusted before it is fetched again.
    catalog_ttl: float = 24 * 3600
    timeout: float = 300.0

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = os.environ.get("DEVDOCS_DATA_DIR")
        return cls(
            data_dir=Path(data_dir).expanduser() if data_dir else default_data_dir(),
            manifest_url=os.environ.get("DEVDOCS_MANIFEST_URL") or DEFAULT_MANIFEST_URL,
            documents_url=(os.environ.get("DEVDOCS_DOCUMENTS_URL") or DEFAULT_DOCUMENTS_URL),
            auto_install=_env_bool("DEVDOCS_AUTO_INSTALL", True),
            catalog_ttl=float(os.environ.get("DEVDOCS_CATALOG_TTL_HOURS") or 24) * 3600,
        )


@dataclass(frozen=True)
class Entry:
    doc: str
    name: str
    path: str
    type: str


@dataclass(frozen=True)
class DocType:
    name: str
    slug: str
    count: int


@dataclass(frozen=True)
class InstalledDoc:
    slug: str
    name: str
    version: str
    release: str
    mtime: int
    db_size: int
    installed_at: float
    offline: bool
    entries: int
    pages: int

    @property
    def title(self) -> str:
        return f"{self.name} {self.version}".strip()


@dataclass(frozen=True)
class InstallResult:
    slug: str
    status: str  # "installed", "updated", "made offline" or "unchanged"
    doc: InstalledDoc


@dataclass(frozen=True)
class Page:
    doc: str
    path: str
    fragment: str
    html: str


@dataclass(frozen=True)
class ContentHit:
    doc: str
    path: str
    title: str
    snippet: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS docs (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    release TEXT NOT NULL,
    mtime INTEGER NOT NULL,
    db_size INTEGER NOT NULL,
    info TEXT NOT NULL,
    installed_at REAL NOT NULL,
    offline INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS types (
    doc TEXT NOT NULL,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (doc, position)
);
CREATE TABLE IF NOT EXISTS entries (
    doc TEXT NOT NULL,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    type TEXT NOT NULL,
    PRIMARY KEY (doc, position)
);
CREATE INDEX IF NOT EXISTS entries_by_type ON entries (doc, type);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    doc TEXT NOT NULL,
    path TEXT NOT NULL,
    html TEXT NOT NULL,
    UNIQUE (doc, path)
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    doc UNINDEXED, path UNINDEXED, title, body, tokenize = 'porter unicode61'
)
"""

_DOCS_COLUMNS = "slug, name, version, release, mtime, db_size, installed_at, offline"
_DEVDOCS_URL_RE = re.compile(r"^https?://(?:www\.)?devdocs\.io/", re.IGNORECASE)


class DevDocsStore:
    def __init__(self, settings: Settings | None = None, http: httpx2.Client | None = None):
        self.settings = settings or Settings.from_env()
        self.settings.documents_url = self.settings.documents_url.rstrip("/")
        self.db_path = self.settings.data_dir / "devdocs.sqlite3"
        self._http_client = http
        self._local = threading.local()
        self._lock = threading.Lock()
        self._doc_locks: dict[str, threading.Lock] = {}
        self._catalog_cache: tuple[float, list[dict]] | None = None
        self._entry_cache: dict[str, tuple[float, list[Entry], list[str]]] = {}
        self.fts_enabled = self._init_db()

    # -- plumbing -------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=60, isolation_level=None)
            # Lets removals hand space back. Only takes effect on a new
            # database, and so must come before the switch to WAL.
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return conn

    def _init_db(self) -> bool:
        conn = self._connect()
        conn.executescript(SCHEMA)
        try:
            conn.execute(FTS_SCHEMA)
        except sqlite3.OperationalError:
            log.warning("SQLite was built without FTS5; full-text search is disabled")
            return False
        return True

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
        if self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    @contextmanager
    def _doc_lock(self, slug: str) -> Iterator[None]:
        with self._lock:
            lock = self._doc_locks.setdefault(slug, threading.Lock())
        with lock:
            yield

    def _http(self) -> httpx2.Client:
        if self._http_client is None:
            self._http_client = httpx2.Client(
                timeout=self.settings.timeout,
                follow_redirects=True,
                headers={"User-Agent": f"devdocs-mcp/{__version__}"},
            )
        return self._http_client

    def _get(self, url: str) -> httpx2.Response:
        try:
            response = self._http().get(url)
        except httpx2.HTTPError as error:
            raise DevDocsError(f"Could not fetch {url}: {error}") from error
        if response.status_code >= 400:
            message = f"Could not fetch {url}: HTTP {response.status_code}"
            if response.status_code == 404:
                raise PageNotFoundError(message)
            raise DevDocsError(message)
        return response

    def _get_json(self, url: str):
        response = self._get(url)
        try:
            return response.json()
        except ValueError as error:
            raise DevDocsError(f"{url} did not return valid JSON") from error

    def _doc_url(self, slug: str, filename: str, mtime: int) -> str:
        return f"{self.settings.documents_url}/{slug}/{filename}?{mtime}"

    # -- catalog --------------------------------------------------------------

    def catalog(self, refresh: bool = False) -> list[dict]:
        """Every doc DevDocs offers, as listed in its docs.json."""
        now = time.time()
        ttl = self.settings.catalog_ttl
        cached = self._catalog_cache
        if cached is not None and not refresh and now - cached[0] < ttl:
            return cached[1]

        conn = self._connect()
        row = conn.execute("SELECT value FROM settings WHERE key = 'catalog'").fetchone()
        stored = json.loads(row[0]) if row else None
        if stored is not None and not refresh and now - stored["fetched_at"] < ttl:
            self._catalog_cache = (stored["fetched_at"], stored["docs"])
            return stored["docs"]

        try:
            docs = self._get_json(self.settings.manifest_url)
            if not isinstance(docs, list):
                raise DevDocsError(f"{self.settings.manifest_url} is not a DevDocs catalog")
        except DevDocsError as error:
            if stored is None:
                raise DevDocsError(f"Could not download the DevDocs catalog. {error}") from error
            log.warning("Using the cached DevDocs catalog: %s", error)
            # Retry after another TTL rather than on every call.
            self._catalog_cache = (now, stored["docs"])
            return stored["docs"]

        docs = [doc for doc in docs if isinstance(doc, dict) and doc.get("slug")]
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('catalog', ?)",
            (json.dumps({"fetched_at": now, "docs": docs}),),
        )
        self._catalog_cache = (now, docs)
        return docs

    def _catalog_entry(self, slug: str) -> dict | None:
        try:
            catalog = self.catalog()
        except DevDocsError:
            return None
        return next((doc for doc in catalog if doc["slug"] == slug), None)

    def resolve(self, name: str) -> dict:
        """Find a doc by slug ("python~3.12"), name ("Python"), alias ("py")
        or name and version ("python 3.12", "python@3").

        A bare name picks the installed version if there is one, otherwise
        the newest version DevDocs has.
        """
        key = " ".join(name.lower().split())
        if not key:
            raise DocNotFoundError("No doc name given.")
        installed = {
            slug: json.loads(info)
            for slug, info in self._connect().execute("SELECT slug, info FROM docs")
        }
        if key in installed:
            return installed[key]
        try:
            catalog = self.catalog()
        except DevDocsError:
            if not installed:
                raise
            catalog = []

        docs = {doc["slug"]: doc for doc in catalog}
        if key in docs:
            return docs[key]
        docs.update(installed)

        def matching(base: str, version: str) -> list[dict]:
            return [
                doc
                for doc in docs.values()
                if base in _doc_names(doc) and _version_matches(_version_slug(doc), version)
            ]

        # The whole key as a name ("ruby on rails"), else a name and a version.
        candidates = matching(key, "")
        versioned = re.fullmatch(r"(.+?)\s*[~@ ]\s*([^~@ ]+)", key)
        if not candidates and versioned:
            candidates = matching(*versioned.groups())
        if candidates:
            return max(
                candidates,
                key=lambda doc: (
                    doc["slug"] in installed,
                    "~" not in doc["slug"],
                    _version_key(doc),
                ),
            )

        known = {name for doc in docs.values() for name in (doc["slug"], *_doc_names(doc))}
        suggestions = difflib.get_close_matches(key, known, n=5, cutoff=0.5)
        hint = f" Did you mean: {', '.join(sorted(suggestions))}?" if suggestions else ""
        raise DocNotFoundError(f"No DevDocs doc matches {name!r}.{hint}")

    # -- installed docs -------------------------------------------------------

    def installed(self) -> list[InstalledDoc]:
        rows = self._connect().execute(
            f"""
            SELECT {_DOCS_COLUMNS},
                   (SELECT count(*) FROM entries WHERE entries.doc = docs.slug),
                   (SELECT count(*) FROM pages WHERE pages.doc = docs.slug)
            FROM docs ORDER BY name, slug
            """
        )
        return [_installed_doc(row) for row in rows]

    def installed_doc(self, slug: str) -> InstalledDoc | None:
        row = (
            self._connect()
            .execute(
                f"""
            SELECT {_DOCS_COLUMNS},
                   (SELECT count(*) FROM entries WHERE entries.doc = docs.slug),
                   (SELECT count(*) FROM pages WHERE pages.doc = docs.slug)
            FROM docs WHERE slug = ?
            """,
                (slug,),
            )
            .fetchone()
        )
        return _installed_doc(row) if row else None

    def install(self, name: str, *, full: bool = True, force: bool = False) -> InstallResult:
        """Download a doc's index and, with `full`, every page for offline use.

        Installing an installed doc updates it when DevDocs has a newer build.
        An offline doc stays offline.
        """
        doc = self.resolve(name)
        slug = doc["slug"]
        with self._doc_lock(slug):
            current = self.installed_doc(slug)
            latest = self._catalog_entry(slug) or doc
            mtime = int(latest.get("mtime") or 0)
            full = full or bool(current and current.offline)
            if (
                current is not None
                and not force
                and current.mtime == mtime
                and (current.offline or not full)
            ):
                return InstallResult(slug, "unchanged", current)

            log.info("Downloading %s%s", slug, " (all pages)" if full else " (index)")
            index = self._get_json(self._doc_url(slug, "index.json", mtime))
            pages = self._get_json(self._doc_url(slug, "db.json", mtime)) if full else None
            if not isinstance(index, dict) or (pages is not None and not isinstance(pages, dict)):
                raise DevDocsError(f"DevDocs returned malformed data for {slug}")
            self._write_doc(latest, index, pages)

        if current is None:
            status = "installed"
        elif current.mtime != mtime or force:
            status = "updated"
        else:
            status = "made offline"
        return InstallResult(slug, status, self.installed_doc(slug))

    def _write_doc(self, info: dict, index: dict, pages: dict[str, str] | None) -> None:
        slug = info["slug"]
        entries = [entry for entry in index.get("entries") or [] if entry.get("path")]
        types = index.get("types") or []
        with self._transaction() as conn:
            self._delete_doc(conn, slug)
            conn.execute(
                "INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    slug,
                    info.get("name") or slug,
                    info.get("version") or "",
                    info.get("release") or "",
                    int(info.get("mtime") or 0),
                    int(info.get("db_size") or 0),
                    json.dumps(info),
                    time.time(),
                    int(pages is not None),
                ),
            )
            conn.executemany(
                "INSERT INTO types VALUES (?, ?, ?, ?, ?)",
                (
                    (slug, i, t.get("name") or "", t.get("slug") or "", int(t.get("count") or 0))
                    for i, t in enumerate(types)
                ),
            )
            conn.executemany(
                "INSERT INTO entries VALUES (?, ?, ?, ?, ?)",
                (
                    (slug, i, e.get("name") or e["path"], e["path"], e.get("type") or "")
                    for i, e in enumerate(entries)
                ),
            )
            if pages:
                titles: dict[str, str] = {}
                for entry in entries:
                    path, _, fragment = entry["path"].partition("#")
                    if not fragment or path not in titles:
                        titles[path] = entry.get("name") or path
                for path, html in pages.items():
                    if isinstance(html, str):
                        self._insert_page(conn, slug, path, html, titles.get(path))
        self._entry_cache.pop(slug, None)

    def _insert_page(
        self, conn: sqlite3.Connection, slug: str, path: str, html: str, title: str | None
    ) -> None:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO pages (doc, path, html) VALUES (?, ?, ?)", (slug, path, html)
        )
        if cursor.rowcount and self.fts_enabled:
            conn.execute(
                "INSERT INTO pages_fts (rowid, doc, path, title, body) VALUES (?, ?, ?, ?, ?)",
                (
                    cursor.lastrowid,
                    slug,
                    path,
                    page_title(html) or title or path,
                    html_to_text(html),
                ),
            )

    def _delete_doc(self, conn: sqlite3.Connection, slug: str) -> None:
        for table in ("docs", "types", "entries", "pages"):
            column = "slug" if table == "docs" else "doc"
            conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (slug,))
        if self.fts_enabled:
            conn.execute("DELETE FROM pages_fts WHERE doc = ?", (slug,))

    def remove(self, name: str) -> str:
        slug = self.resolve(name)["slug"]
        with self._doc_lock(slug):
            if self.installed_doc(slug) is None:
                raise DocNotFoundError(f"{slug} is not installed.")
            with self._transaction() as conn:
                self._delete_doc(conn, slug)
        self._entry_cache.pop(slug, None)
        # Hand the freed space back to the filesystem: merge away the
        # full-text index's delete markers, vacuum the free pages (stepping
        # the pragma to completion), then checkpoint so the file shrinks.
        conn = self._connect()
        if self.fts_enabled:
            conn.execute("INSERT INTO pages_fts (pages_fts) VALUES ('optimize')")
        conn.execute("PRAGMA incremental_vacuum").fetchall()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        return slug

    def outdated(self) -> list[tuple[InstalledDoc, dict]]:
        """Installed docs that DevDocs has rebuilt since they were downloaded."""
        latest = {doc["slug"]: doc for doc in self.catalog()}
        return [
            (doc, latest[doc.slug])
            for doc in self.installed()
            if doc.slug in latest and int(latest[doc.slug].get("mtime") or 0) != doc.mtime
        ]

    def ensure_installed(self, names: Iterable[str]) -> list[str]:
        """Resolve doc names to installed slugs, fetching indexes if allowed."""
        slugs: list[str] = []
        for name in names:
            doc = self.resolve(name)
            slug = doc["slug"]
            if self.installed_doc(slug) is None:
                if not self.settings.auto_install:
                    raise DocNotFoundError(
                        f"{slug} is not installed. Install it first (install_docs)."
                    )
                self.install(slug, full=False)
            if slug not in slugs:
                slugs.append(slug)
        return slugs

    def scope(self, docs: Iterable[str] | None) -> list[str]:
        """The installed slugs to search: the named docs, or every installed one."""
        docs = [doc for doc in docs or [] if doc.strip()]
        if docs:
            return self.ensure_installed(docs)
        slugs = [doc.slug for doc in self.installed()]
        if not slugs:
            raise DevDocsError(
                "No docs are installed yet. Name the docs to search (they are fetched "
                "on first use), or install some first. list_docs shows what is available."
            )
        return slugs

    # -- reading --------------------------------------------------------------

    def _entries(self, slug: str) -> tuple[list[Entry], list[str]]:
        conn = self._connect()
        row = conn.execute("SELECT installed_at FROM docs WHERE slug = ?", (slug,)).fetchone()
        stamp = row[0] if row else 0.0
        cached = self._entry_cache.get(slug)
        if cached is not None and cached[0] == stamp:
            return cached[1], cached[2]
        entries = [
            Entry(slug, name, path, type_)
            for name, path, type_ in conn.execute(
                "SELECT name, path, type FROM entries WHERE doc = ? ORDER BY position", (slug,)
            )
        ]
        texts = [normalize_string(entry.name) for entry in entries]
        self._entry_cache[slug] = (stamp, entries, texts)
        return entries, texts

    def search(
        self, query: str, docs: Iterable[str] | None = None, limit: int = 20
    ) -> list[tuple[Entry, int]]:
        """DevDocs-style search over entry names (functions, classes, guides...)."""
        items: list[Entry] = []
        texts: list[str] = []
        for slug in self.scope(docs):
            entries, normalized = self._entries(slug)
            items.extend(entries)
            texts.extend(normalized)
        return rank_entries(items, texts, query, limit)

    def search_content(
        self, query: str, docs: Iterable[str] | None = None, limit: int = 10
    ) -> list[ContentHit]:
        """Full-text search over the pages stored locally."""
        if not self.fts_enabled:
            raise DevDocsError("Full-text search needs SQLite with FTS5, which this Python lacks.")
        terms = re.findall(r"\w+", query)
        if not terms:
            raise DevDocsError("The query has no words to search for.")
        slugs = self.scope(docs)
        placeholders = ", ".join("?" * len(slugs))
        sql = f"""
            SELECT doc, path, title, snippet(pages_fts, 3, '**', '**', ' … ', 24)
            FROM pages_fts
            WHERE pages_fts MATCH ? AND doc IN ({placeholders})
            ORDER BY bm25(pages_fts, 0.0, 0.0, 10.0, 1.0)
            LIMIT ?
        """
        conn = self._connect()
        quoted = [f'"{term}"' for term in terms]
        rows = conn.execute(sql, (" ".join(quoted), *slugs, limit)).fetchall()
        if not rows and len(terms) > 1:
            rows = conn.execute(sql, (" OR ".join(quoted), *slugs, limit)).fetchall()
        return [ContentHit(*row) for row in rows]

    def types(self, name: str) -> tuple[str, list[DocType]]:
        slug = self.ensure_installed([name])[0]
        rows = self._connect().execute(
            "SELECT name, slug, count FROM types WHERE doc = ? ORDER BY position", (slug,)
        )
        return slug, [DocType(*row) for row in rows]

    def entries(self, name: str, type_: str | None = None) -> tuple[str, list[Entry]]:
        """A doc's entries, optionally only those of one type (case-insensitive)."""
        slug = self.ensure_installed([name])[0]
        entries, _ = self._entries(slug)
        if type_ is not None:
            wanted = type_.strip().lower()
            type_slugs = {
                type_name.lower()
                for type_name, type_slug in self._connect().execute(
                    "SELECT name, slug FROM types WHERE doc = ?", (slug,)
                )
                if type_slug.lower() == wanted
            }
            entries = [
                entry
                for entry in entries
                if entry.type.lower() == wanted or entry.type.lower() in type_slugs
            ]
        return slug, entries

    def page(self, name: str, path: str) -> Page:
        """A page's HTML, from the local store or, failing that, from DevDocs.

        `path` is an entry path such as "library/os#os.getcwd"; a devdocs.io
        URL works too and names its own doc.
        """
        url = _split_devdocs_url(path)
        if url is not None:
            name, path = url
        slug = self.ensure_installed([name])[0]
        page_path, _, fragment = path.strip().partition("#")
        candidates = _page_path_candidates(page_path)

        conn = self._connect()
        for candidate in candidates:
            row = conn.execute(
                "SELECT html FROM pages WHERE doc = ? AND path = ?", (slug, candidate)
            ).fetchone()
            if row:
                return Page(slug, candidate, fragment, row[0])

        entries = self._entries(slug)[0]
        doc = self.installed_doc(slug)
        if doc is not None and not doc.offline:
            for candidate in candidates:
                filename = quote(candidate, safe="/~:@!$&'()*+,;=") + ".html"
                try:
                    html = self._get(self._doc_url(slug, filename, doc.mtime)).text
                except PageNotFoundError:
                    continue
                title = next((e.name for e in entries if e.path == candidate), None)
                with self._transaction() as conn:
                    self._insert_page(conn, slug, candidate, html, title)
                return Page(slug, candidate, fragment, html)

        known = {entry.path.partition("#")[0] for entry in entries}
        suggestions = difflib.get_close_matches(candidates[0], known, n=5, cutoff=0.5)
        hint = f" Similar paths: {', '.join(suggestions)}." if suggestions else ""
        raise PageNotFoundError(
            f"No page {candidates[0]!r} in {slug}.{hint} Use search_docs to find entry paths."
        )


def _split_devdocs_url(url: str) -> tuple[str, str] | None:
    """devdocs.io/<slug>/<path>#<fragment> -> (slug, path#fragment)"""
    url = url.strip()
    if not _DEVDOCS_URL_RE.match(url):
        return None
    parts = urlsplit(url)
    slug, _, path = parts.path.lstrip("/").partition("/")
    return slug, f"{path}#{parts.fragment}" if parts.fragment else path


def _doc_names(doc: dict) -> set[str]:
    names = {doc["slug"].partition("~")[0], (doc.get("name") or "").lower()}
    if doc.get("alias"):
        names.add(str(doc["alias"]).lower())
    names.discard("")
    return names


def _version_slug(doc: dict) -> str:
    return doc["slug"].partition("~")[2]


def _version_matches(version_slug: str, wanted: str) -> bool:
    """ "3" matches "3.12" and "3_lts", but not "30"."""
    return (
        not wanted
        or version_slug == wanted
        or version_slug.startswith((wanted + ".", wanted + "_"))
    )


def _version_key(doc: dict) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", _version_slug(doc)))


def _page_path_candidates(path: str) -> list[str]:
    path = path.strip().lstrip("/")
    if path.endswith(".html"):
        path = path[: -len(".html")]
    if not path:
        return ["index"]
    candidates = [path]
    if path.endswith("/"):
        candidates += [path + "index", path.rstrip("/")]
    elif path.endswith("/index"):
        candidates.append(path[: -len("index")])
    return candidates


def _installed_doc(row: tuple) -> InstalledDoc:
    slug, name, version, release, mtime, db_size, installed_at, offline, entries, pages = row
    return InstalledDoc(
        slug, name, version, release, mtime, db_size, installed_at, bool(offline), entries, pages
    )
