"""Where things live, from the environment."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    # The knowledge folder: everything docshelf serves comes from here.
    knowledge_dir: Path
    # The search index, a rebuildable cache. Defaults to <knowledge_dir>/.docshelf.
    index_dir: Path
    # Subfolder that pages from the browser extension and the crawler are saved in.
    web_folder: str = "web"
    # Token the HTTP API requires; generated and kept in index_dir when unset.
    token: str | None = None

    @classmethod
    def from_env(
        cls, knowledge_dir: Path | str | None = None, index_dir: Path | str | None = None
    ) -> Settings:
        knowledge = Path(
            knowledge_dir or os.environ.get("DOCSHELF_DIR") or Path.home() / "knowledge"
        ).expanduser()
        index = Path(
            index_dir or os.environ.get("DOCSHELF_INDEX_DIR") or knowledge / ".docshelf"
        ).expanduser()
        return cls(
            knowledge_dir=knowledge,
            index_dir=index,
            web_folder=(os.environ.get("DOCSHELF_WEB_FOLDER") or "web").strip("/"),
            token=os.environ.get("DOCSHELF_TOKEN") or None,
        )

    def api_token(self) -> str:
        """The API token: DOCSHELF_TOKEN, or one generated on first use and kept."""
        if self.token:
            return self.token
        path = self.index_dir / "token"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "x", encoding="utf-8") as file:
                os.chmod(path, 0o600)
                file.write(secrets.token_urlsafe(24) + "\n")
        except FileExistsError:
            pass
        token = path.read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError(f"{path} is empty; delete it to generate a new token")
        return token
