"""Turn DevDocs page HTML into Markdown an LLM can read."""

from __future__ import annotations

import html as htmllib
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from markdownify import MarkdownConverter

HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
BLOCK_TAGS = set(HEADINGS) | {
    "address", "article", "aside", "blockquote", "dd", "details", "div", "dl", "dt",
    "figure", "footer", "header", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "tbody", "td", "th", "thead", "tr", "ul",
}  # fmt: skip
_DROP_TAGS = ["script", "style", "noscript", "svg", "iframe", "template"]

# Any URL with a scheme ("https:", "mailto:", ...) or protocol-relative ("//host").
_ABSOLUTE_URL_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")
_SPACE_RE = re.compile(r"\s+")
_BASE = "https://doc.invalid/"


class _Converter(MarkdownConverter):
    # Most docs write API reference as <dl><dt>signature</dt><dd>description</dd>.
    # Markdown has no definition lists, and the indented pandoc form buries
    # code blocks, so signatures become bold lines over plain descriptions.
    def convert_dt(self, el, text, parent_tags):
        text = _SPACE_RE.sub(" ", text or "").strip()
        if "_inline" in parent_tags:
            return f" {text} "
        return f"\n\n**{text}**\n\n" if text else "\n"

    def convert_dd(self, el, text, parent_tags):
        text = (text or "").strip()
        if "_inline" in parent_tags:
            return f" {text} "
        return f"\n\n{text}\n\n" if text else "\n"

    def convert_img(self, el, text, parent_tags):
        # Inline data: images are base64 noise to a language model.
        if el.get("src", "").startswith("data:"):
            return el.get("alt", "")
        return super().convert_img(el, text, parent_tags)


def _code_language(el: Tag) -> str:
    language = el.get("data-language") or ""
    if not language:
        for cls in el.get("class") or []:
            if cls.startswith(("language-", "lang-")):
                language = cls.split("-", 1)[1]
                break
    return language if isinstance(language, str) else ""


def resolve_link(href: str, page_path: str) -> str:
    """Resolve a link found on `page_path` to a path inside the same doc.

    Absolute URLs are returned unchanged.
    """
    if _ABSOLUTE_URL_RE.match(href):
        return href
    if href.startswith("#"):
        return page_path + href
    resolved = urljoin(_BASE + page_path, href)
    path = resolved[len(_BASE) :] if resolved.startswith(_BASE) else urlsplit(resolved).path
    return path or "index"


def find_section(soup: BeautifulSoup, fragment: str) -> list[Tag] | None:
    """Return the elements that document the anchor `fragment`, or None.

    A heading takes everything up to the next heading of the same or a higher
    level; a `<dt>` (the API-reference style most docs use) takes its
    signature lines plus their `<dd>` descriptions; anything else is returned
    as its enclosing block.
    """
    target = soup.find(id=fragment) or soup.find(attrs={"name": fragment})
    if not isinstance(target, Tag):
        return None

    element = target
    while element.name not in BLOCK_TAGS and isinstance(element.parent, Tag):
        if element.parent.name == "[document]":
            break
        element = element.parent
    if not element.get_text(strip=True):
        # An empty anchor that marks the element after it.
        following = element.find_next_sibling()
        if isinstance(following, Tag):
            element = following

    if element.name in HEADINGS:
        return _heading_section(element)
    if element.name == "dt":
        return _definition_section(element)
    return [element]


def _heading_level(tag: Tag) -> int | None:
    return int(tag.name[1]) if tag.name in HEADINGS else None


def _heading_section(heading: Tag) -> list[Tag]:
    parent = heading.parent
    if (
        isinstance(parent, Tag)
        and parent.name in {"section", "article", "div"}
        and parent.find(True) is heading
    ):
        # The heading opens a wrapper element: the wrapper is the section.
        return [parent]

    level = _heading_level(heading)
    section = [heading]
    for sibling in heading.find_next_siblings():
        sibling_level = _heading_level(sibling)
        if sibling_level is not None and sibling_level <= level:
            break
        nested = sibling.find(HEADINGS)
        if nested is not None and _heading_level(nested) <= level:
            break
        section.append(sibling)
    return section


def _definition_section(term: Tag) -> list[Tag]:
    section = [term]
    seen_description = False
    for sibling in term.find_next_siblings():
        if sibling.name == "dt" and seen_description:
            break
        if sibling.name not in {"dt", "dd"}:
            break
        seen_description = seen_description or sibling.name == "dd"
        section.append(sibling)
    return section


def html_to_markdown(html: str, page_path: str, fragment: str | None = None) -> tuple[str, bool]:
    """Convert a page to Markdown.

    Relative links are rewritten to doc-relative paths (e.g.
    `library/os.path#os.path.join`) that can be passed back to `read_page`.
    With `fragment`, only the section that documents that anchor is returned;
    the second value says whether the anchor was found.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    for anchor in soup.find_all("a", href=True):
        anchor["href"] = resolve_link(anchor["href"], page_path)

    found = False
    if fragment:
        section = find_section(soup, fragment)
        if section is not None:
            found = True
            html = "".join(str(element) for element in section)
            soup = BeautifulSoup(html, "html.parser")

    markdown = _Converter(
        heading_style="ATX",
        bullets="-",
        code_language_callback=_code_language,
        escape_misc=False,
    ).convert_soup(soup)
    return _tidy(markdown), found


def _tidy(markdown: str) -> str:
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip() + "\n"


_BLOCK_TAG_RE = re.compile(
    r"</?(?:blockquote|br|dd|div|dl|dt|h[1-6]|li|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SKIP_BLOCKS_RE = re.compile(r"<(script|style|svg)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


def html_to_text(html: str) -> str:
    """Fast, lossy plain-text extraction used for the full-text index."""
    text = _SKIP_BLOCKS_RE.sub(" ", html)
    text = _BLOCK_TAG_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    return _SPACE_RE.sub(" ", htmllib.unescape(text)).strip()


_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


def page_title(html: str) -> str | None:
    match = _H1_RE.search(html)
    if match is None:
        return None
    title = html_to_text(match.group(1)).rstrip("¶#").strip()
    return title or None
