"""Doctor per-check timing and the over-budget report (FOC-doctor-slow).

`ddflow doctor` ran over 14 minutes on a 116 MB log with nothing saying where
the time went. Doctor now times each named check: every check's elapsed seconds
are recorded on the outcome (`check_timings`), and a check that runs longer than
`[health].check_budget_s` is reported in `over_budget` (and as a note) instead of
being a silent wait.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import reporting
from ddflow.services import embed as EMB


def _slow_doctor_notes(_cfg) -> list[str]:
    time.sleep(1.5)
    return []


def test_a_check_over_its_budget_is_reported(repo, monkeypatch):
    assert run_cli(repo, "init")[0] == 0
    with (repo / ".ddflow" / "config.toml").open("a", encoding="utf-8") as f:
        f.write("\n[health]\ncheck_budget_s = 1\n")
    monkeypatch.setattr(EMB, "doctor_notes", _slow_doctor_notes)
    out = reporting.doctor(repo, agent="a1")
    assert "embed" in out.data["check_timings"], out.data["check_timings"]
    over = [n for n in out.data["over_budget"] if "embed" in n]
    assert over, out.data["over_budget"]


def test_a_check_under_budget_is_not_reported(repo):
    assert run_cli(repo, "init")[0] == 0
    out = reporting.doctor(repo, agent="a1")
    assert "embed" in out.data["check_timings"], out.data["check_timings"]
    over = [n for n in out.data["over_budget"] if "embed" in n]
    assert not over, out.data["over_budget"]
