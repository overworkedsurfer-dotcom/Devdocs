"""Read files from the knowledge folder as Markdown, split into sections.

Every supported file becomes a `Document`: its Markdown text plus the
sections its headings divide it into. Sections are what search finds and
what `read` can return on their own, addressed by their heading's anchor
(GitHub-style: "## Rolling back" -> "rolling-back").
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .webpage import page_from_html

KINDS = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".txt": "text",
    ".text": "text",
    ".rst": "text",
    ".adoc": "text",
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
}
MAX_FILE_BYTES = 50 * 1024 * 1024

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_ATX_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


@dataclass(frozen=True)
class Section:
    anchor: str  # "" for text before the first heading
    heading: str
    level: int  # 1-6, 0 for text before the first heading
    start: int  # first line
    end: int  # line after the last, before any subsection


@dataclass(frozen=True)
class Document:
    title: str
    source: str  # the URL a saved web page came from
    markdown: str
    sections: list[Section]

    @property
    def lines(self) -> list[str]:
        return self.markdown.splitlines()

    def section_text(self, anchor: str) -> str | None:
        """The section with `anchor`, including its subsections."""
        lines = self.lines
        for i, section in enumerate(self.sections):
            if section.anchor == anchor:
                end = len(lines)
                for following in self.sections[i + 1 :]:
                    if following.level <= section.level:
                        end = following.start
                        break
                return "\n".join(lines[section.start : end]).strip() + "\n"
        return None

    def section_body(self, section: Section) -> str:
        """A section's own text, without subsections, for the search index."""
        text = "\n".join(self.lines[section.start : section.end])
        return _LINK_RE.sub(r"\1", text)


def kind_of(path: Path) -> str | None:
    return KINDS.get(path.suffix.lower())


def load(path: Path) -> Document:
    """Read a supported file. Raises ValueError for unsupported files."""
    kind = kind_of(path)
    if kind is None:
        raise ValueError(f"Unsupported file type: {path.name}")
    if kind == "pdf":
        return _load_pdf(path)

    text = path.read_text(encoding="utf-8", errors="replace")
    if kind == "html":
        page = page_from_html(text)
        return from_markdown(page.markdown, fallback_title=page.title or path.stem)
    if kind == "text":
        lines = text.splitlines()
        first_line = next((line.strip() for line in lines if line.strip()), "")
        sections = [Section("", "", 0, 0, len(lines))] if first_line else []
        return Document(first_line[:200] or path.stem, "", text, sections)
    return from_markdown(text, fallback_title=path.stem)


def from_markdown(text: str, fallback_title: str = "") -> Document:
    meta, body = split_front_matter(text)
    sections = split_sections(body)
    first_h1 = next((s.heading for s in sections if s.level == 1), "")
    title = meta.get("title") or first_h1 or fallback_title
    source = meta.get("source") or meta.get("url") or ""
    return Document(title, source, body, sections)


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split simple YAML front matter (key: value lines) from Markdown."""
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t", "-", "#")):
            meta[key.strip().lower()] = _unquote(value.strip())
    return meta, text[match.end() :]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)  # docshelf writes titles JSON-quoted
        except ValueError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def split_sections(markdown: str) -> list[Section]:
    """Divide Markdown at its ATX headings, skipping fenced code blocks."""
    lines = markdown.splitlines()
    sections: list[Section] = []
    starts: list[tuple[int, str, int]] = []  # (line, heading, level)
    fence = ""
    for i, line in enumerate(lines):
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        heading = _ATX_RE.match(line)
        if heading:
            starts.append((i, clean_heading(heading.group(2)), len(heading.group(1))))

    used: dict[str, int] = {}
    first = starts[0][0] if starts else len(lines)
    if any(line.strip() for line in lines[:first]):
        sections.append(Section("", "", 0, 0, first))
    for n, (start, heading, level) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        sections.append(Section(_unique(slugify(heading), used), heading, level, start, end))
    return sections


def clean_heading(text: str) -> str:
    text = _LINK_RE.sub(r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("`", "").strip(" *_")


def slugify(heading: str) -> str:
    """GitHub's heading anchors: lowercase, punctuation dropped, spaces to hyphens."""
    heading = unicodedata.normalize("NFKC", heading).lower()
    heading = re.sub(r"[^\w\- ]", "", heading)
    return heading.replace(" ", "-")


def _unique(slug: str, used: dict[str, int]) -> str:
    if slug not in used:
        used[slug] = 0
        return slug
    while True:
        used[slug] += 1
        candidate = f"{slug}-{used[slug]}"
        if candidate not in used:
            used[candidate] = 0
            return candidate


def _load_pdf(path: Path) -> Document:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    title = ""
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title).strip()
    title = title or path.stem
    parts = [f"# {title}"]
    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(f"## Page {number}\n\n{text}")
    return from_markdown("\n\n".join(parts) + "\n", fallback_title=title)
