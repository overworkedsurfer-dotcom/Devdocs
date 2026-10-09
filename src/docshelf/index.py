"""The search index over the knowledge folder.

A SQLite database with one row per file and one full-text (FTS5) row per
section. It is only a cache: `sync` compares the folder with what was
indexed (by modification time and size) and catches up, so files can be
added, edited and deleted by anything, at any time.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .documents import MAX_FILE_BYTES, kind_of, load

log = logging.getLogger(__name__)

SKIP_DIRS = {"node_modules", "__pycache__"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    error TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS files_by_source ON files (source);
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL,
    anchor TEXT NOT NULL,
    title TEXT NOT NULL,
    heading TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sections_by_file ON sections (file_id);
CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
    title, heading, body,
    content = 'sections', content_rowid = 'id', tokenize = 'porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS sections_added AFTER INSERT ON sections BEGIN
    INSERT INTO sections_fts (rowid, title, heading, body)
    VALUES (new.id, new.title, new.heading, new.body);
END;
CREATE TRIGGER IF NOT EXISTS sections_removed AFTER DELETE ON sections BEGIN
    INSERT INTO sections_fts (sections_fts, rowid, title, heading, body)
    VALUES ('delete', old.id, old.title, old.heading, old.body);
END;
"""


@dataclass(frozen=True)
class Hit:
    path: str
    title: str
    source: str
    anchor: str
    heading: str
    snippet: str


@dataclass
class SyncResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


class Index:
    def __init__(self, root: Path, db_path: Path, min_interval: float = 2.0):
        self.root = root
        self.db_path = db_path
        # Searches re-check the folder at most this often (seconds).
        self.min_interval = min_interval
        self._local = threading.local()
        self._sync_lock = threading.Lock()
        self._last_sync = float("-inf")
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
        except sqlite3.OperationalError as error:
            if "fts5" in str(error).lower():
                raise RuntimeError(
                    "docshelf needs SQLite with FTS5, which this Python lacks."
                ) from error
            raise

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=60, isolation_level=None)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

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

    # -- keeping up with the folder ------------------------------------------

    def scan(self) -> dict[str, tuple[int, int]]:
        """Every indexable file under the root: path -> (mtime_ns, size)."""
        found: dict[str, tuple[int, int]] = {}
        for directory, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
            )
            for filename in filenames:
                if filename.startswith(".") or kind_of(Path(filename)) is None:
                    continue
                path = Path(directory, filename)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size <= MAX_FILE_BYTES:
                    found[path.relative_to(self.root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
        return found

    def sync(self, force: bool = False) -> SyncResult:
        """Index new and changed files and forget deleted ones."""
        result = SyncResult()
        with self._sync_lock:
            if not force and time.monotonic() - self._last_sync < self.min_interval:
                return result
            on_disk = self.scan()
            indexed = {
                path: (mtime_ns, size)
                for path, mtime_ns, size in self._connect().execute(
                    "SELECT path, mtime_ns, size FROM files"
                )
            }
            removed = [path for path in indexed if path not in on_disk]
            if removed:
                with self._transaction() as conn:
                    for path in removed:
                        self._forget(conn, path)
                result.removed = removed
            for path, stamp in on_disk.items():
                if indexed.get(path) != stamp:
                    ok = self._index(path, *stamp)
                    (result.updated if path in indexed else result.added).append(path)
                    if not ok:
                        result.failed.append(path)
            self._last_sync = time.monotonic()
        if result.added or result.updated or result.removed:
            log.info(
                "Indexed %d new, %d changed, %d removed files",
                len(result.added),
                len(result.updated),
                len(result.removed),
            )
        return result

    def index_file(self, path: str) -> None:
        """Index one file now, e.g. right after writing it."""
        stat = (self.root / path).stat()
        with self._sync_lock:
            self._index(path, stat.st_mtime_ns, stat.st_size)

    def _index(self, path: str, mtime_ns: int, size: int) -> bool:
        error = ""
        try:
            document = load(self.root / path)
            title, source = document.title, document.source
            rows = [
                (s.anchor, title, s.heading, document.section_body(s)) for s in document.sections
            ]
        except Exception as exc:  # a bad file must not stop the rest
            log.warning("Could not index %s: %s", path, exc)
            title, source, rows, error = Path(path).stem, "", [], str(exc) or type(exc).__name__
        with self._transaction() as conn:
            row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
            if row:
                file_id = row[0]
                conn.execute("DELETE FROM sections WHERE file_id = ?", (file_id,))
                conn.execute(
                    "UPDATE files SET mtime_ns = ?, size = ?, title = ?, source = ?, error = ? "
                    "WHERE id = ?",
                    (mtime_ns, size, title, source, error, file_id),
                )
            else:
                file_id = conn.execute(
                    "INSERT INTO files (path, mtime_ns, size, title, source, error) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (path, mtime_ns, size, title, source, error),
                ).lastrowid
            conn.executemany(
                "INSERT INTO sections (file_id, anchor, title, heading, body) "
                "VALUES (?, ?, ?, ?, ?)",
                [(file_id, *row) for row in rows],
            )
        return not error

    def _forget(self, conn: sqlite3.Connection, path: str) -> None:
        row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        if row:
            conn.execute("DELETE FROM sections WHERE file_id = ?", (row[0],))
            conn.execute("DELETE FROM files WHERE id = ?", (row[0],))

    # -- queries --------------------------------------------------------------

    def search(self, query: str, folder: str | None = None, limit: int = 10) -> list[Hit]:
        """Sections matching all the words (then word prefixes, then any word),
        best first, at most two per file."""
        terms = re.findall(r"\w+", query)
        if not terms:
            raise ValueError("The query has no words to search for.")
        quoted = [f'"{term}"' for term in terms]
        attempts = [" ".join(quoted), " ".join(q + "*" for q in quoted)]
        if len(terms) > 1:
            attempts.append(" OR ".join(q + "*" for q in quoted))

        where, params = "", []
        prefix = _folder_prefix(folder)
        if prefix:
            where = "AND substr(f.path, 1, ?) = ?"
            params = [len(prefix), prefix]
        sql = f"""
            SELECT f.path, f.title, f.source, s.anchor, s.heading,
                   snippet(sections_fts, 2, '**', '**', ' … ', 24)
            FROM sections_fts
            JOIN sections s ON s.id = sections_fts.rowid
            JOIN files f ON f.id = s.file_id
            WHERE sections_fts MATCH ? {where}
            ORDER BY bm25(sections_fts, 8.0, 4.0, 1.0)
            LIMIT ?
        """
        conn = self._connect()
        for match in attempts:
            rows = conn.execute(sql, (match, *params, limit * 4)).fetchall()
            if rows:
                break
        hits: list[Hit] = []
        per_file: dict[str, int] = {}
        for row in rows:
            hit = Hit(*row)
            if per_file.get(hit.path, 0) < 2:
                per_file[hit.path] = per_file.get(hit.path, 0) + 1
                hits.append(hit)
            if len(hits) == limit:
                break
        return hits

    def file(self, path: str) -> tuple[str, str] | None:
        """(title, source) of an indexed file."""
        row = (
            self._connect()
            .execute("SELECT title, source FROM files WHERE path = ?", (path,))
            .fetchone()
        )
        return (row[0], row[1]) if row else None

    def path_for_source(self, url: str) -> str | None:
        row = (
            self._connect()
            .execute("SELECT path FROM files WHERE source = ? ORDER BY path LIMIT 1", (url,))
            .fetchone()
        )
        return row[0] if row else None

    def similar(self, path: str, n: int = 5) -> list[str]:
        paths = [row[0] for row in self._connect().execute("SELECT path FROM files")]
        return difflib.get_close_matches(path, paths, n=n, cutoff=0.5)

    def listing(self, folder: str | None = None) -> tuple[dict[str, int], list[tuple[str, str]]]:
        """A folder's subfolders (with file counts) and files (with titles)."""
        prefix = _folder_prefix(folder)
        rows = self._connect().execute(
            "SELECT path, title FROM files WHERE substr(path, 1, ?) = ? ORDER BY path",
            (len(prefix), prefix),
        )
        folders: dict[str, int] = {}
        files: list[tuple[str, str]] = []
        for path, title in rows:
            rest = path[len(prefix) :]
            if "/" in rest:
                name = rest.split("/", 1)[0]
                folders[name] = folders.get(name, 0) + 1
            else:
                files.append((path, title))
        return folders, files

    def count(self) -> int:
        return self._connect().execute("SELECT count(*) FROM files").fetchone()[0]


def _folder_prefix(folder: str | None) -> str:
    folder = (folder or "").strip().strip("/")
    return f"{folder}/" if folder else ""
