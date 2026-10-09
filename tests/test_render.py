from conftest import JAVASCRIPT_PAGES, PYTHON_PAGES

from devdocs_mcp.render import html_to_markdown, html_to_text, page_title, resolve_link


def test_resolve_link():
    assert resolve_link("os.path#os.path.join", "library/os") == "library/os.path#os.path.join"
    assert resolve_link("#os.getcwd", "library/os") == "library/os#os.getcwd"
    assert resolve_link("../library/os#os.getcwd", "library/os.path") == "library/os#os.getcwd"
    assert resolve_link("../index", "library/os") == "index"
    assert resolve_link("https://peps.python.org/", "library/os") == "https://peps.python.org/"
    assert resolve_link("mailto:a@b.c", "x") == "mailto:a@b.c"
    assert resolve_link("//cdn.example.com/a", "x") == "//cdn.example.com/a"


def test_definition_section_stops_at_the_next_definition():
    markdown, found = html_to_markdown(PYTHON_PAGES["library/os"], "library/os", "os.getcwd")
    assert found
    assert "Return a string representing the current working directory." in markdown
    assert "bytestring" not in markdown
    assert "chdir" not in markdown


def test_heading_section_stops_at_the_next_heading_of_its_level():
    markdown, found = html_to_markdown(
        PYTHON_PAGES["library/os"], "library/os", "files-and-directories"
    )
    assert found
    assert markdown.startswith("## Files and Directories")
    assert "os.getcwdb" in markdown
    assert "Process Parameters" not in markdown

    page = JAVASCRIPT_PAGES["global_objects/array/map"]
    markdown, _ = html_to_markdown(page, "global_objects/array/map", "examples")
    assert "Mapping an array of numbers" in markdown
    assert "See also" not in markdown


def test_missing_fragment_returns_the_whole_page():
    markdown, found = html_to_markdown(PYTHON_PAGES["library/os"], "library/os", "nope")
    assert not found
    assert "Process Parameters" in markdown


def test_markdown_rewrites_links_and_keeps_code():
    markdown, _ = html_to_markdown(PYTHON_PAGES["library/os"], "library/os")
    assert "# os — Miscellaneous operating system interfaces" in markdown
    assert "(library/os.path#os.path.join)" in markdown
    assert "(library/os#os.getcwd)" in markdown
    assert "(https://peps.python.org/pep-0008/)" in markdown

    markdown, _ = html_to_markdown(PYTHON_PAGES["library/json"], "library/json")
    assert "**json.dumps(*obj*)**\n\nSerialize *obj* to a JSON formatted `str`." in markdown
    assert '\n```python\njson.dumps({"a": 1})\n```' in markdown
    assert "base64" not in markdown


def test_plain_text_and_title():
    assert html_to_text("<p>a &amp; <b>b</b></p><script>x()</script>") == "a & b"
    assert (
        html_to_text("<dt>os.<span>chdir</span>(<em>path</em>)</dt><dd>x</dd>")
        == "os.chdir(path) x"
    )
    assert page_title(PYTHON_PAGES["library/json"]) == "json — JSON encoder and decoder"
    assert page_title("<p>no heading</p>") is None
