"""B382471e10a: the rendered board computes its critical path with the project's config, so
`[schedule].docs_ignore_phase_needs = false` shapes it as it shapes `ddflow next`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.views import markdown as MD


def test_board_passes_its_config_to_the_critical_path(repo, monkeypatch):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P-a", "--title", "a")
    run_cli(repo, "task", "add", "A1", "--phase", "P-a", "--globs", "core/a.py")
    seen: list = []
    real = MD.critical_path

    def spy(state, phase="", cfg=None):
        seen.append(cfg)
        return real(state, phase, cfg)

    monkeypatch.setattr(MD, "critical_path", spy)
    cfg = Config()
    cfg.schedule.docs_ignore_phase_needs = False
    MD.board(fold(EventLog(repo, "t").read_all(), strict=False), cfg)
    assert seen == [cfg], "the board used the default knob, not the project's"


def _queue(repo) -> None:
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P-gate", "--title", "gate")
    run_cli(repo, "task", "add", "G1", "--phase", "P-gate", "--globs", "core/g.py")
    run_cli(repo, "phase", "add", "P-next", "--title", "next", "--needs", "P-gate")
    for i in (1, 2, 3):
        needs = ("--needs", f"D{i - 1}") if i > 1 else ()
        run_cli(
            repo, "task", "add", f"D{i}", "--phase", "P-next", "--globs", f"docs/d{i}.md", *needs
        )


def _depth(repo, cfg: Config) -> int:
    text = MD.board(fold(EventLog(repo, "t").read_all(), strict=False), cfg)
    line = next(ln for ln in text.splitlines() if ln.startswith("**Critical path**"))
    return int(line.split("(")[1].split()[0])


def test_the_rendered_critical_path_follows_docs_ignore_phase_needs(repo):
    """Three chained doc tasks in a phase that needs a code phase: the docs ignore the phase's
    need or inherit it (a longer path, behind G1), by the knob."""
    _queue(repo)
    ignoring, binding = Config(), Config()
    binding.schedule.docs_ignore_phase_needs = False
    assert _depth(repo, ignoring) == 3
    assert _depth(repo, binding) > _depth(repo, ignoring)
