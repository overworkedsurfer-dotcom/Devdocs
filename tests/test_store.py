import pytest

from devdocs_mcp.store import DevDocsError, DocNotFoundError, PageNotFoundError


def test_resolve_names_aliases_and_versions(store):
    assert store.resolve("python~3.11")["slug"] == "python~3.11"
    assert store.resolve("Python")["slug"] == "python~3.12"
    assert store.resolve("py")["slug"] == "python~3.12"
    assert store.resolve("python 3.11")["slug"] == "python~3.11"
    assert store.resolve("python@2")["slug"] == "python~2.7"
    assert store.resolve("js")["slug"] == "javascript"
    assert store.resolve("Ruby on Rails")["slug"] == "rails~7.1"
    assert store.resolve("ruby on rails 7.1")["slug"] == "rails~7.1"
    assert store.resolve("rails@7")["slug"] == "rails~7.1"
    with pytest.raises(DocNotFoundError):
        store.resolve("python 4")
    with pytest.raises(DocNotFoundError, match="python"):
        store.resolve("pyhton")


def test_resolve_prefers_the_installed_version(store):
    store.install("python~3.11", full=False)
    assert store.resolve("python")["slug"] == "python~3.11"


def test_full_install_stores_everything(store, fake):
    result = store.install("python")
    assert (result.slug, result.status) == ("python~3.12", "installed")
    assert result.doc.offline
    assert result.doc.entries == 10
    assert result.doc.pages == 5

    assert store.install("python").status == "unchanged"
    assert fake.count("db.json") == 1

    page = store.page("python", "library/os#os.getcwd")
    assert (page.path, page.fragment) == ("library/os", "os.getcwd")
    assert fake.count(".html") == 0


def test_index_only_install_fetches_pages_lazily(store, fake):
    store.install("javascript", full=False)
    assert fake.count("db.json") == 0

    page = store.page("js", "global_objects/array/map.html#syntax")
    assert "map(callbackFn)" in page.html
    store.page("js", "/global_objects/array/map")
    assert fake.count("map.html") == 1
    assert store.installed_doc("javascript").pages == 1

    with pytest.raises(PageNotFoundError, match="global_objects/array/map"):
        store.page("javascript", "global_objects/array/mapp")

    result = store.install("javascript")
    assert result.status == "made offline"
    assert (result.doc.offline, result.doc.pages) == (True, 3)


def test_docs_are_fetched_on_first_use(store, fake):
    results = store.search("getcwd", ["python~3.12"])
    assert [entry.name for entry, _ in results] == ["os.getcwd()", "os.getcwdb()"]
    assert fake.count("python~3.12/index.json") == 1
    assert not store.installed_doc("python~3.12").offline


def test_auto_install_can_be_disabled(store):
    store.settings.auto_install = False
    with pytest.raises(DocNotFoundError, match="not installed"):
        store.search("map", ["javascript"])


def test_search_spans_installed_docs(store):
    with pytest.raises(DevDocsError, match="No docs are installed"):
        store.search("map")
    store.install("python", full=False)
    store.install("javascript", full=False)
    names = [entry.name for entry, _ in store.search("map")]
    assert names[0] == "Array.prototype.map()"
    assert "Array.prototype.flatMap()" in names


def test_devdocs_urls_name_their_doc(store):
    page = store.page("ignored", "https://devdocs.io/python~3.12/library/os.path#os.path.join")
    assert (page.doc, page.path, page.fragment) == (
        "python~3.12",
        "library/os.path",
        "os.path.join",
    )


def test_full_text_search(store):
    store.install("python")
    hits = store.search_content("serialize JSON objects")
    assert hits[0].path == "library/json"
    assert hits[0].title == "json — JSON encoder and decoder"
    assert "**" in hits[0].snippet
    # No page has every word, so any word will do.
    assert store.search_content("bytestring serialize")


def test_entries_and_types(store):
    slug, types = store.types("python")
    assert slug == "python~3.12"
    assert [t.name for t in types] == ["Built-in Functions", "json", "os", "os.path"]
    _, entries = store.entries("python", "OS.PATH")
    assert [e.name for e in entries] == ["os.path", "os.path.join()"]
    _, entries = store.entries("python", "os-path")
    assert len(entries) == 2


def test_update_and_remove(store, fake):
    store.install("python")
    store.page("python", "library/os")
    fake.catalog[1]["mtime"] += 1
    store.catalog(refresh=True)
    assert [doc.slug for doc, _ in store.outdated()] == ["python~3.12"]

    result = store.install("python", full=False)
    assert result.status == "updated"
    assert result.doc.offline  # an offline doc stays offline
    assert store.outdated() == []

    assert store._connect().execute("PRAGMA auto_vacuum").fetchone() == (2,)  # incremental
    assert store.remove("python") == "python~3.12"
    assert store.installed() == []
    with pytest.raises(DocNotFoundError):
        store.remove("python")


def test_catalog_survives_the_network_going_away(store, fake):
    store.install("javascript", full=False)
    fake.offline = True
    store._catalog_cache = None
    store.settings.catalog_ttl = 0
    assert len(store.catalog()) == 5
    assert store.resolve("js")["slug"] == "javascript"


def test_installed_docs_resolve_without_a_catalog(store, fake):
    store.install("javascript", full=False)
    store._connect().execute("DELETE FROM settings")
    store._catalog_cache = None
    fake.offline = True
    assert store.resolve("javascript")["slug"] == "javascript"
    with pytest.raises(DevDocsError):
        store.install("python")
