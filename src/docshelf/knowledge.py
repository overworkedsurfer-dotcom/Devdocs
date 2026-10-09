"""The knowledge folder: reading, searching, and saving web pages into it."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .documents import load, split_front_matter
from .index import Hit, Index
from .webpage import normalize_url, page_from_html, path_for_url

# Pages with less readable text than this (a login wall, an empty app shell)
# are not saved.
MIN_TEXT_LENGTH = 50


class NotFoundError(Exception):
    pass


@dataclass(frozen=True)
class Reading:
    path: str
    title: str
    source: str
    text: str
    anchor: str  # the #section asked for, if any
    anchor_found: bool


@dataclass(frozen=True)
class SavedPage:
    status: str  # "added", "updated", "unchanged" or "skipped"
    url: str
    path: str | None
    title: str
    links: list[str]


class KnowledgeBase:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.root = self.settings.knowledge_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings.index_dir.mkdir(parents=True, exist_ok=True)
        if self.settings.index_dir.resolve().is_relative_to(self.root.resolve()):
            # Keep the index out of git if the knowledge folder is a repository.
            gitignore = self.settings.index_dir / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text("*\n", encoding="utf-8")
        self.index = Index(self.root, self.settings.index_dir / "index.sqlite3")
        self._write_lock = threading.Lock()

    def close(self) -> None:
        self.index.close()

    # -- reading --------------------------------------------------------------

    def search(self, query: str, folder: str | None = None, limit: int = 10) -> list[Hit]:
        self.index.sync()
        return self.index.search(query, folder, limit)

    def listing(self, folder: str | None = None) -> tuple[dict[str, int], list[tuple[str, str]]]:
        self.index.sync()
        return self.index.listing(folder)

    def count(self) -> int:
        self.index.sync()
        return self.index.count()

    def read(self, target: str) -> Reading:
        """Read a file by its path in the folder ("notes/deploy.md"), optionally
        one section of it ("notes/deploy.md#rolling-back"), or a saved web page
        by its URL."""
        self.index.sync()
        path, anchor = self.resolve(target)
        document = load(self.root / path)
        text = document.section_text(anchor) if anchor else None
        return Reading(
            path,
            document.title,
            document.source,
            text if text is not None else document.markdown,
            anchor,
            text is not None,
        )

    def resolve(self, target: str) -> tuple[str, str]:
        target = target.strip()
        if target.startswith(("http://", "https://")):
            url, _, anchor = target.partition("#")
            try:
                url = normalize_url(url)
            except ValueError as error:
                raise NotFoundError(str(error)) from error
            path = self.index.path_for_source(url)
            if path is None and self.index.file(path_for_url(url, self.settings.web_folder)):
                path = path_for_url(url, self.settings.web_folder)
            if path is None:
                raise NotFoundError(f"No page saved from {url}.")
            return path, anchor

        path, _, anchor = target.partition("#")
        path = path.strip().replace("\\", "/").lstrip("/")
        # Only indexed files can be read, which also keeps reads inside the folder.
        for candidate in (path, f"{path}.md"):
            if candidate and self.index.file(candidate):
                return candidate, anchor
        similar = self.index.similar(path)
        hint = f" Similar: {', '.join(similar)}." if similar else ""
        raise NotFoundError(f"No file {path!r} in the knowledge folder.{hint}")

    # -- saving web pages -----------------------------------------------------

    def save_page(
        self,
        url: str,
        html: str,
        *,
        title: str | None = None,
        selector: str | None = None,
        via: str = "browser",
    ) -> SavedPage:
        """Save a web page as Markdown under the web folder (web/<site>/<path>.md).

        Saving the same page again only rewrites the file when its content
        changed. Raises ValueError for a URL that is not http(s).
        """
        page = page_from_html(html, url, title, selector)
        if page.text_length < MIN_TEXT_LENGTH:
            return SavedPage("skipped", page.url, None, page.title, page.links)

        path = path_for_url(page.url, self.settings.web_folder)
        target = self.root / path
        with self._write_lock:
            existed = target.exists()
            if existed:
                _, old_body = split_front_matter(target.read_text(encoding="utf-8"))
                if old_body.strip() == page.markdown.strip():
                    return SavedPage("unchanged", page.url, path, page.title, page.links)
            front_matter = "\n".join(
                [
                    "---",
                    f"title: {json.dumps(page.title, ensure_ascii=False)}",
                    f"source: {page.url}",
                    f"saved: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
                    f"via: {via}",
                    "---",
                    "",
                ]
            )
            _write_atomic(target, front_matter + page.markdown)
            self.index.index_file(path)
        status = "updated" if existed else "added"
        return SavedPage(status, page.url, path, page.title, page.links)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
