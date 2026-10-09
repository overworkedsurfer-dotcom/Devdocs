import pytest

from docshelf.webpage import normalize_url, page_from_html, path_for_url, site_of

PAGE = """<!doctype html>
<html><head><title>useState – React</title><base href="https://react.dev/reference/"></head>
<body>
<header><a href="/">React</a> <a href="/learn">Learn</a></header>
<nav class="sidebar"><a href="react/useEffect">useEffect</a><a href="react/useMemo#notes">useMemo</a></nav>
<main>
  <article>
    <h1>useState</h1>
    <p>useState is a React Hook that lets you add a state variable to your component.
       See <a href="react/useReducer">useReducer</a> and <a href="#usage">usage</a>.</p>
    <div class="toc">On this page</div>
    <h2 id="usage">Usage</h2>
    <pre data-language="js">const [count, setCount] = useState(0);</pre>
    <img src="data:image/png;base64,AAAA" alt="diagram">
    <img src="/images/flow.png" alt="flow" srcset="a.png 1x, b.png 2x">
    <dl><dt><code>setState(next)</code></dt><dd><p>Updates the state.</p></dd></dl>
    <button>Copy</button>
    <script>track()</script>
  </article>
</main>
<footer>© Meta</footer>
</body></html>"""


def test_normalize_url():
    assert normalize_url("HTTPS://React.dev:443/learn?utm_source=x&a=1#top") == (
        "https://react.dev/learn?a=1"
    )
    assert normalize_url("http://localhost:3000") == "http://localhost:3000/"
    assert site_of("http://wiki.local:8080/a") == "wiki.local:8080"
    with pytest.raises(ValueError):
        normalize_url("chrome://extensions")


def test_path_for_url():
    assert path_for_url("https://react.dev/reference/react/useState") == (
        "web/react.dev/reference/react/useState.md"
    )
    assert path_for_url("https://react.dev/") == "web/react.dev/index.md"
    assert path_for_url("https://example.com/docs/") == "web/example.com/docs/index.md"
    assert path_for_url("https://example.com/a/b.html") == "web/example.com/a/b.md"
    assert path_for_url("http://wiki.local:8080/x?id=7") == "web/wiki.local_8080/x__id=7.md"
    assert path_for_url("https://example.com/what%3F/a:b") == "web/example.com/what_/a_b.md"
    assert path_for_url("https://example.com/p", folder="saved") == "saved/example.com/p.md"


def test_page_keeps_content_and_drops_furniture():
    page = page_from_html(PAGE, "https://react.dev/reference/react/useState")
    assert page.title == "useState"
    assert page.markdown.startswith("# useState")
    for gone in ("Learn", "useEffect", "On this page", "Copy", "track()", "© Meta", "base64"):
        assert gone not in page.markdown
    assert "[useReducer](https://react.dev/reference/react/useReducer)" in page.markdown
    assert "[usage](https://react.dev/reference/react/useState#usage)" in page.markdown
    assert "![flow](https://react.dev/images/flow.png)" in page.markdown
    assert "```js\nconst [count, setCount] = useState(0);\n```" in page.markdown
    assert "**`setState(next)`**\n\nUpdates the state." in page.markdown


def test_links_cover_the_whole_page_for_crawling():
    page = page_from_html(PAGE, "https://react.dev/reference/react/useState")
    assert "https://react.dev/reference/react/useEffect" in page.links
    assert "https://react.dev/reference/react/useMemo" in page.links  # fragment dropped
    assert "https://react.dev/learn" in page.links


def test_title_falls_back_to_the_document_title():
    page = page_from_html("<title>Notes</title><p>" + "word " * 50 + "</p>", "https://x.dev/n")
    assert page.title == "Notes"
    assert page.markdown.startswith("# Notes\n")
