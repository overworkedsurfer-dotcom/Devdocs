from conftest import make_pdf, write

from docshelf.documents import from_markdown, load, slugify, split_front_matter

RUNBOOK = """---
title: "Deploying \\"the\\" API"
source: https://wiki.example.com/deploy
tags: [ops]
---

Intro paragraph.

# Deploying

## Rolling back

Undo with `kubectl rollout undo`.

### Checking the rollback

Watch the pods.

```bash
# not a heading
kubectl get pods
```

## Rolling back

A second section with the same heading.

## Monitoring

Dashboards.
"""


def test_front_matter():
    meta, body = split_front_matter(RUNBOOK)
    assert meta["title"] == 'Deploying "the" API'
    assert meta["source"] == "https://wiki.example.com/deploy"
    assert body.startswith("\nIntro paragraph.")
    assert split_front_matter("# No front matter\n") == ({}, "# No front matter\n")


def test_sections_and_anchors():
    document = from_markdown(RUNBOOK)
    assert document.title == 'Deploying "the" API'
    assert document.source == "https://wiki.example.com/deploy"
    assert [(s.anchor, s.level) for s in document.sections] == [
        ("", 0),
        ("deploying", 1),
        ("rolling-back", 2),
        ("checking-the-rollback", 3),
        ("rolling-back-1", 2),
        ("monitoring", 2),
    ]


def test_section_text_includes_subsections_but_not_siblings():
    document = from_markdown(RUNBOOK)
    text = document.section_text("rolling-back")
    assert text.startswith("## Rolling back")
    assert "Watch the pods." in text
    assert "# not a heading" in text
    assert "second section" not in text
    assert document.section_text("missing") is None
    # The index gets a section's own text, without subsections.
    own = document.section_body(document.sections[2])
    assert "Undo with" in own and "Watch the pods" not in own


def test_slugify_matches_github():
    assert slugify("Rolling back") == "rolling-back"
    assert slugify("What's new in v2.0?") == "whats-new-in-v20"
    assert slugify("useState()") == "usestate"
    assert slugify("Über uns") == "über-uns"


def test_title_falls_back_to_first_h1_then_filename():
    assert from_markdown("# Hello\n\ntext").title == "Hello"
    assert from_markdown("text only", fallback_title="notes").title == "notes"


def test_load_text_html_and_pdf(folder):
    text = load(write(folder, "a.txt", "\n\nShopping list\nmilk\n"))
    assert text.title == "Shopping list"
    assert len(text.sections) == 1

    html = load(
        write(
            folder,
            "b.html",
            "<title>T</title><nav>menu</nav><main><h1>Guide</h1><p>Body text.</p></main>",
        )
    )
    assert html.title == "Guide"
    assert "menu" not in html.markdown
    assert "Body text." in html.markdown

    pdf_path = folder / "c.pdf"
    pdf_path.write_bytes(make_pdf("Failover runbook", ["Promote the replica", "Repoint DNS"]))
    pdf = load(pdf_path)
    assert pdf.title == "Failover runbook"
    assert [s.anchor for s in pdf.sections] == ["failover-runbook", "page-1", "page-2"]
    assert "Promote the replica" in pdf.section_text("page-1")
