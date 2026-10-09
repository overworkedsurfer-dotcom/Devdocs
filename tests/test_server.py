import pytest
from conftest import write
from mcp import Client

from docshelf.server import create_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(knowledge, tool, **arguments):
    async with Client(create_server(knowledge)) as client:
        result = await client.call_tool(tool, arguments)
    return result.is_error, "\n".join(block.text for block in result.content)


async def test_tools_are_listed(knowledge):
    async with Client(create_server(knowledge)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"search", "read", "list_files", "crawl_site", "crawl_status"}
    assert tools["search"].annotations.read_only_hint
    assert not tools["crawl_site"].annotations.read_only_hint


async def test_search_then_read(knowledge, folder):
    write(folder, "notes/deploy.md", "# Deploy\n\n## Rolling back\n\nUse kubectl rollout undo.\n")
    error, text = await call(knowledge, "search", query="rollout")
    assert not error
    assert "- notes/deploy.md#rolling-back — Deploy › Rolling back" in text

    error, text = await call(knowledge, "read", path="notes/deploy.md#rolling-back")
    assert text.startswith("path: notes/deploy.md\n(Only the #rolling-back section")
    assert "kubectl rollout undo" in text

    error, text = await call(knowledge, "read", path="nope.md")
    assert error and "No file" in text


async def test_read_in_chunks(knowledge, folder):
    write(folder, "long.md", "# Long\n\n" + "line of text\n" * 2000)
    error, text = await call(knowledge, "read", path="long.md", max_length=1000)
    offset = int(text.rsplit("offset=", 1)[1].split()[0])
    error, rest = await call(knowledge, "read", path="long.md", offset=offset)
    assert not error and "line of text" in rest


async def test_list_files(knowledge, folder):
    error, text = await call(knowledge, "list_files")
    assert text == "The knowledge folder is empty."
    write(folder, "notes/a.md", "# A note\n\ntext")
    write(folder, "notes/b.md", "# B note\n\ntext")
    write(folder, "readme.md", "# Readme\n\ntext")
    error, text = await call(knowledge, "list_files")
    assert text == "(top level):\n- notes/ (2 files)\n- readme.md — Readme"
    error, text = await call(knowledge, "list_files", folder="notes/")
    assert "- notes/a.md — A note" in text
    error, text = await call(knowledge, "list_files", folder="nope")
    assert error


async def test_crawl_tools_validate(knowledge):
    error, text = await call(knowledge, "crawl_site", url="ftp://example.com/")
    assert error and "Not a web page URL" in text
    error, text = await call(knowledge, "crawl_status")
    assert text == "No crawls have run."
