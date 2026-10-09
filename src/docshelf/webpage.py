"""Turn a web page into a clean Markdown document.

Used for pages the browser extension sends, pages the crawler fetches, and
.html files in the knowledge folder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment, Tag
from markdownify import MarkdownConverter

# Tried in order; the first that holds most of the page's text is the content.
CONTENT_SELECTORS = [
    "main", "[role=main]", "article", "#content", "#main-content", ".main-content",
    ".markdown-body", ".content", "#main", ".document", ".documentation",
]  # fmt: skip
# Page furniture, removed from the content before conversion.
NOISE_SELECTORS = [
    "script", "style", "noscript", "iframe", "form", "button", "svg", "canvas",
    "template", "object", "embed", "dialog", "nav", "footer", "aside",
    "[role=navigation]", "[role=banner]", "[role=contentinfo]", "[role=search]",
    "[aria-hidden=true]", ".sidebar", ".toc", ".breadcrumb", ".breadcrumbs",
]  # fmt: skip
_TRACKING_PARAM_RE = re.compile(r"^(utm_\w+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid)$")
_SPACE_RE = re.compile(r"\s+")
_UNSAFE_PATH_CHARS_RE = re.compile(r'[<>:"\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    markdown: str
    links: list[str]
    text_length: int


def normalize_url(url: str) -> str:
    """Canonical form of a page URL: no fragment, tracking parameters or default port.

    Raises ValueError for anything but an http(s) URL.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        raise ValueError(f"Not a web page URL: {url!r}")
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port is not None and (scheme, port) not in (("http", 80), ("https", 443)):
        host = f"{host}:{port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    query = [(k, v) for k, v in query if not _TRACKING_PARAM_RE.match(k)]
    return urlunsplit((scheme, host, parts.path or "/", urlencode(query), ""))


def site_of(url: str) -> str:
    return urlsplit(normalize_url(url)).netloc


def path_for_url(url: str, folder: str = "web") -> str:
    """Where a page is saved in the knowledge folder, e.g.
    https://react.dev/reference/react/useState -> web/react.dev/reference/react/useState.md
    """
    parts = urlsplit(normalize_url(url))
    segments = [_safe_segment(unquote(s)) for s in parts.path.split("/") if s]
    if not segments or parts.path.endswith("/"):
        segments.append("index")
    name = re.sub(r"\.(html?|php|aspx?)$", "", segments[-1], flags=re.IGNORECASE) or "index"
    if parts.query:
        name += "__" + _safe_segment(parts.query)[:60]
    segments[-1] = name + ".md"
    site = _safe_segment(parts.netloc.replace(":", "_"))
    return str(PurePosixPath(folder, site, *segments))


def _safe_segment(segment: str) -> str:
    segment = _UNSAFE_PATH_CHARS_RE.sub("_", segment).strip(" .")
    return segment[:120] or "_"


def page_from_html(
    html: str, url: str | None = None, title: str | None = None, selector: str | None = None
) -> WebPage:
    """Extract a page's main content as Markdown.

    Navigation, sidebars, footers and scripts are dropped; links and images
    are made absolute when `url` is known. `links` lists every http(s) link
    on the whole page (fragments removed), for crawling.
    """
    soup = BeautifulSoup(html, "html.parser")
    base = url or ""
    base_tag = soup.find("base", href=True)
    if url and isinstance(base_tag, Tag):
        base = urljoin(url, base_tag["href"])

    links = _links(soup, base) if base else []
    document_title = title or (soup.title.get_text(" ", strip=True) if soup.title else "")

    root = _content_root(soup, selector)
    for element in root.select(", ".join(NOISE_SELECTORS)):
        element.decompose()
    if root.name in ("body", "[document]"):
        # A site header outside any article; keep headers that hold the title.
        for header in root.find_all("header"):
            if not header.find("h1"):
                header.decompose()
    for comment in root.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    if base:
        _absolutize(root, base, url or base)

    h1 = root.find("h1")
    page_title = _clean_title(h1.get_text(" ", strip=True) if h1 else "") or _clean_title(
        document_title
    )
    markdown = _to_markdown(root)
    if page_title and h1 is None:
        markdown = f"# {page_title}\n\n{markdown}"
    text_length = len(_SPACE_RE.sub(" ", root.get_text(" ", strip=True)))
    return WebPage(normalize_url(url) if url else "", page_title, markdown, links, text_length)


def _content_root(soup: BeautifulSoup, selector: str | None) -> Tag:
    if selector:
        found = soup.select_one(selector)
        if isinstance(found, Tag):
            return found
    body = soup.body or soup
    body_length = len(body.get_text(" ", strip=True))
    for candidate_selector in CONTENT_SELECTORS:
        for candidate in soup.select(candidate_selector):
            length = len(candidate.get_text(" ", strip=True))
            if length >= 200 and length >= 0.3 * body_length:
                return candidate
    return body


def _links(soup: BeautifulSoup, base: str) -> list[str]:
    links: dict[str, None] = {}
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base, anchor["href"].strip()).partition("#")[0]
        if href.startswith(("http://", "https://")):
            links[href] = None
    return list(links)


def _absolutize(root: Tag, base: str, page_url: str) -> None:
    for anchor in root.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.lower().startswith(("javascript:", "data:")):
            del anchor["href"]
        elif href.startswith("#"):
            anchor["href"] = page_url.partition("#")[0] + href
        else:
            anchor["href"] = urljoin(base, href)
    for image in root.find_all(["img", "source"]):
        src = image.get("src", "")
        if src.startswith("data:"):
            del image["src"]
        elif src:
            image["src"] = urljoin(base, src)
        if image.get("srcset"):
            del image["srcset"]


def _clean_title(title: str) -> str:
    return _SPACE_RE.sub(" ", title).strip().rstrip("¶#").strip()


class _Converter(MarkdownConverter):
    # API reference is often <dl><dt>signature</dt><dd>description</dd>.
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
        if not el.get("src"):
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


def _to_markdown(root: Tag) -> str:
    markdown = _Converter(
        heading_style="ATX",
        bullets="-",
        code_language_callback=_code_language,
        escape_misc=False,
    ).convert_soup(root)
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip() + "\n"
