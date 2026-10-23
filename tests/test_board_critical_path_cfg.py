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
