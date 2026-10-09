import os

import pytest
from conftest import write

from docshelf.knowledge import NotFoundError

ARTICLE = """<html><head><title>Caching guide</title></head><body>
<nav><a href="/docs/">Docs home</a><a href="/docs/next">Next</a></nav>
<main><h1>Caching guide</h1>
<p>How the CDN caches responses and how to purge stale content from the edge.</p>
<h2>Purging</h2><p>Call the purge endpoint with the surrogate key to evict cached objects.</p>
</main></body></html>"""


def test_sync_follows_the_folder(knowledge, folder):
    write(folder, "notes/a.md", "# Alpha\n\nfirst note about kubernetes")
    write(folder, ".hidden/secret.md", "# Secret\n\nkubernetes")
    write(folder, "node_modules/pkg/readme.md", "# Pkg\n\nkubernetes")
    write(folder, "image.png", "not text")
    assert [h.path for h in knowledge.search("kubernetes")] == ["notes/a.md"]

    write(folder, "notes/a.md", "# Alpha\n\nnow about terraform")
    os.utime(folder / "notes/a.md", ns=(1, 1))  # a different mtime, whatever the clock
    assert knowledge.search("kubernetes") == []
    assert knowledge.search("terraform")[0].path == "notes/a.md"

    (folder / "notes/a.md").unlink()
    assert knowledge.search("terraform") == []
    assert knowledge.count() == 0


def test_search_finds_sections(knowledge, folder):
    write(
        folder,
        "ops/runbook.md",
        "# Runbook\n\nGeneral notes.\n\n## Database failover\n\nPromote the replica.\n\n"
        "## Cache\n\nFlush redis.\n",
    )
    write(folder, "team/people.md", "# People\n\nAsk Sam about the database.\n")
    hits = knowledge.search("database failover")
    assert (hits[0].path, hits[0].anchor, hits[0].heading) == (
        "ops/runbook.md",
        "database-failover",
        "Database failover",
    )
    assert "**" in hits[0].snippet
    # Prefixes, then any word, when nothing has every word.
    assert knowledge.search("failov")[0].anchor == "database-failover"
    assert {h.path for h in knowledge.search("redis people")} == {
        "ops/runbook.md",
        "team/people.md",
    }
    # Folder filter.
    assert [h.path for h in knowledge.search("database", folder="team")] == ["team/people.md"]
    with pytest.raises(ValueError):
        knowledge.search("!!!")


def test_at_most_two_sections_per_file(knowledge, folder):
    write(folder, "big.md", "".join(f"## Part {i}\n\nwidget details\n\n" for i in range(6)))
    write(folder, "small.md", "# Small\n\nwidget\n")
    paths = [h.path for h in knowledge.search("widget")]
    assert paths.count("big.md") == 2
    assert "small.md" in paths


def test_read(knowledge, folder):
    write(
        folder,
        "notes/deploy.md",
        "# Deploy\n\nIntro.\n\n## Rolling back\n\nUndo it.\n\n## Next\n\nMore.",
    )
    reading = knowledge.read("notes/deploy.md#rolling-back")
    assert (reading.path, reading.title, reading.anchor_found) == (
        "notes/deploy.md",
        "Deploy",
        True,
    )
    assert reading.text == "## Rolling back\n\nUndo it.\n"
    assert knowledge.read("/notes/deploy").path == "notes/deploy.md"  # .md optional
    missing = knowledge.read("notes/deploy.md#nope")
    assert not missing.anchor_found and "More." in missing.text

    with pytest.raises(NotFoundError, match="notes/deploy.md"):
        knowledge.read("notes/deplyo.md")


def test_reads_stay_inside_the_folder(knowledge, folder, tmp_path):
    (tmp_path / "outside.md").write_text("# Outside\n\nsecret")
    with pytest.raises(NotFoundError):
        knowledge.read("../outside.md")
    with pytest.raises(NotFoundError):
        knowledge.read(str(tmp_path / "outside.md"))


def test_save_page(knowledge, folder):
    url = "https://cdn.example.com/docs/caching?utm_source=feed"
    saved = knowledge.save_page(url, ARTICLE)
    assert (saved.status, saved.path, saved.title) == (
        "added",
        "web/cdn.example.com/docs/caching.md",
        "Caching guide",
    )
    text = (folder / saved.path).read_text()
    assert text.startswith(
        '---\ntitle: "Caching guide"\nsource: https://cdn.example.com/docs/caching\n'
    )
    assert "Docs home" not in text
    assert "https://cdn.example.com/docs/next" in saved.links

    assert knowledge.save_page(url, ARTICLE).status == "unchanged"
    changed = ARTICLE.replace("surrogate key", "surrogate key or URL")
    assert knowledge.save_page(url, changed).status == "updated"

    # Saved pages are searchable right away, and readable by URL.
    assert knowledge.search("surrogate")[0].path == saved.path
    reading = knowledge.read("https://cdn.example.com/docs/caching#purging")
    assert reading.source == "https://cdn.example.com/docs/caching"
    assert reading.text.startswith("## Purging")

    empty = knowledge.save_page("https://cdn.example.com/login", "<p>Sign in</p>")
    assert (empty.status, empty.path) == ("skipped", None)
    with pytest.raises(ValueError):
        knowledge.save_page("file:///etc/passwd", ARTICLE)


def test_index_is_kept_out_of_git(tmp_path):
    from docshelf.config import Settings
    from docshelf.knowledge import KnowledgeBase

    kb = KnowledgeBase(Settings.from_env(knowledge_dir=tmp_path / "kb"))
    assert (tmp_path / "kb/.docshelf/.gitignore").read_text() == "*\n"
    assert kb.settings.api_token() == kb.settings.api_token()  # generated once, then kept
    kb.close()
