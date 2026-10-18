"""An upgrade moves an old hook command line to ``hooks run <event> --harness <id>``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import claudehooks as CH


def _commands(repo: Path, rel: str) -> list[str]:
    doc = json.loads((repo / rel).read_text())
    return [h["command"] for gs in doc["hooks"].values() for g in gs for h in g["hooks"]]


def _old_line(repo: Path, spec: CH.HookSpec) -> None:
    """Rewrite ``spec``'s installed hook as an older ddflow wrote it (no stamp, old subcommand)."""
    path = repo / spec.file
    doc = json.loads(path.read_text())
    for g in doc["hooks"][spec.event]:
        for h in g["hooks"]:
            if CH._ours(h, spec.marker):
                h["command"] = f"ddflow hooks {spec.name}" + (
                    " --gemini" if spec.agent == "gemini" else ""
                )
    path.write_text(json.dumps(doc))


def test_the_installed_lines_use_the_shared_entry_point(repo):
    assert run_cli(repo, "hooks", "install", "--claude", "--gemini")[0] == 0
    for h in CH.HOOKS:
        assert any(h.subcommand in c for c in _commands(repo, h.file)), h.name
        assert not CH.stale(repo, h)


def test_an_old_line_is_stale_and_install_rewrites_it_in_place(repo):
    run_cli(repo, "hooks", "install", "--claude", "--gemini")
    for h in CH.HOOKS:
        _old_line(repo, h)
        assert CH.stale(repo, h), h.name
    assert run_cli(repo, "hooks", "install", "--claude", "--gemini")[0] == 0
    for h in CH.HOOKS:
        assert not CH.stale(repo, h), h.name
    assert len(_commands(repo, ".claude/settings.json")) == len(
        [h for h in CH.HOOKS if h.file == ".claude/settings.json"]
    )


def test_a_missing_hook_is_not_stale(repo):
    assert not any(CH.stale(repo, h) for h in CH.HOOKS)
