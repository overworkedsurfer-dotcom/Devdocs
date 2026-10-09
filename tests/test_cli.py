from pathlib import Path

import pytest

from docshelf.cli import build_parser, main


@pytest.mark.parametrize(
    "argv",
    [
        ["--dir", "/tmp/kb", "-v", "ls"],
        ["ls", "--dir", "/tmp/kb", "-v"],
        ["-v", "serve", "--dir", "/tmp/kb"],
    ],
)
def test_global_options_go_before_or_after_the_command(argv):
    args = build_parser().parse_args(argv)
    assert args.dir == Path("/tmp/kb")
    assert args.verbose


def test_serve_defaults(monkeypatch):
    for name in ("DOCSHELF_TRANSPORT", "DOCSHELF_HOST", "DOCSHELF_PORT"):
        monkeypatch.delenv(name, raising=False)
    args = build_parser().parse_args(["serve"])
    assert (args.transport, args.host, args.port) == ("stdio", "127.0.0.1", 8000)


def test_commands(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DOCSHELF_TOKEN", raising=False)
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes/todo.md").write_text("# Todo\n\n## Groceries\n\nBuy oat milk.\n")

    assert main(["search", "oat milk", "--dir", str(kb)]) == 0
    assert "notes/todo.md#groceries" in capsys.readouterr().out
    assert main(["read", "notes/todo.md#groceries", "--dir", str(kb)]) == 0
    assert "Buy oat milk." in capsys.readouterr().out
    assert main(["read", "missing.md", "--dir", str(kb)]) == 1
    assert main(["token", "--dir", str(kb)]) == 0
    token = capsys.readouterr().out.strip()
    assert len(token) > 20 and (kb / ".docshelf/token").read_text().strip() == token
