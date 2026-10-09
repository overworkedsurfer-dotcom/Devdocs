import pytest
from mcp import Client

from devdocs_mcp.server import create_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(store, tool, **arguments):
    async with Client(create_server(store)) as client:
        result = await client.call_tool(tool, arguments)
    text = "\n".join(block.text for block in result.content)
    return result.is_error, text


async def test_tools_are_listed(store):
    async with Client(create_server(store)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {
        "search_docs",
        "read_page",
        "search_content",
        "list_entries",
        "list_docs",
        "install_docs",
        "remove_docs",
    }
    assert tools["search_docs"].annotations.read_only_hint
    assert tools["remove_docs"].annotations.destructive_hint
    assert "docs" in tools["search_docs"].input_schema["properties"]


async def test_search_then_read(store):
    error, text = await call(store, "search_docs", query="getcwd", docs=["python"])
    assert not error
    assert "- os.getcwd() [os] doc=python~3.12 path=library/os#os.getcwd" in text

    error, text = await call(store, "read_page", doc="python~3.12", path="library/os#os.getcwd")
    assert not error
    assert "https://devdocs.io/python~3.12/library/os#os.getcwd" in text
    assert "current working directory" in text
    assert "bytestring" not in text

    error, text = await call(
        store, "read_page", doc="python", path="library/os#os.getcwd", full_page=True
    )
    assert "bytestring" in text


async def test_read_page_in_chunks(store):
    error, text = await call(
        store, "read_page", doc="js", path="global_objects/array/map", max_length=1000
    )
    assert not error
    assert "offset=" not in text  # the page fits

    store.install("python")
    store._connect().execute(
        "UPDATE pages SET html = ? WHERE path = 'index'", ("<p>" + "word " * 2000 + "</p>",)
    )
    error, text = await call(store, "read_page", doc="python", path="index", max_length=1000)
    assert "offset=" in text
    offset = int(text.rsplit("offset=", 1)[1].split()[0])
    error, rest = await call(store, "read_page", doc="python", path="index", offset=offset)
    assert not error
    assert "word" in rest


async def test_errors_are_reported_to_the_model(store):
    error, text = await call(store, "search_docs", query="x", docs=["no-such-doc"])
    assert error
    assert "No DevDocs doc matches" in text

    error, text = await call(store, "read_page", doc="python", path="library/nope")
    assert error
    assert "No page" in text


async def test_list_docs_install_and_remove(store):
    error, text = await call(store, "list_docs")
    assert "Installed (0)" in text
    assert "JavaScript, Python" in text

    error, text = await call(store, "install_docs", docs=["python", "nope"])
    assert not error
    assert "python~3.12: installed, 10 entries, all 5 pages offline" in text
    assert "nope: failed" in text

    error, text = await call(store, "list_docs", query="python")
    assert "- python~3.12: Python 3.12 (release 3.12.1) [installed:" in text
    assert "- python~2.7: Python 2.7 (release 2.7.18), 3 KB offline" in text

    error, text = await call(store, "search_content", query="working directory")
    assert "library/os" in text

    error, text = await call(store, "remove_docs", docs=["python"])
    assert "python~3.12: removed" in text


async def test_list_entries(store):
    error, text = await call(store, "list_entries", doc="python")
    assert "python~3.12 has 4 sections" in text
    assert "- os.path (2)" in text

    error, text = await call(store, "list_entries", doc="python", type="os.path")
    assert "- os.path.join() path=library/os.path#os.path.join" in text

    error, text = await call(store, "list_entries", doc="python", type="nope")
    assert error


async def test_search_content_notes_partial_docs(store):
    await call(store, "read_page", doc="js", path="global_objects/array/map")
    error, text = await call(store, "search_content", query="callbackFn", docs=["js"])
    assert "global_objects/array/map" in text
    assert "javascript is not installed offline" in text
