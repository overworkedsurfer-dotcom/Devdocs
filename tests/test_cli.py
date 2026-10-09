from pathlib import Path

import pytest

from devdocs_mcp.cli import build_parser


@pytest.mark.parametrize(
    "argv",
    [
        ["--data-dir", "/tmp/x", "-v", "list"],
        ["list", "--data-dir", "/tmp/x", "-v"],
        ["-v", "serve", "--data-dir", "/tmp/x"],
    ],
)
def test_global_options_go_before_or_after_the_command(argv):
    args = build_parser().parse_args(argv)
    assert args.data_dir == Path("/tmp/x")
    assert args.verbose


def test_serve_defaults(monkeypatch):
    monkeypatch.delenv("DEVDOCS_TRANSPORT", raising=False)
    args = build_parser().parse_args(["serve"])
    assert (args.transport, args.host, args.port) == ("stdio", "127.0.0.1", 8000)
    assert not hasattr(args, "data_dir")
