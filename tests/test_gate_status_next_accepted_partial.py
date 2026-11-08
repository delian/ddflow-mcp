"""B-gate-status-next-ignores-accepted-partial: `gate status` said `next = ci` for an
auto-accepted baseline-red partial.

`gate run` records a partial ci/unit_tests whose failures also fail on the base and, with
`[gates].baseline_red_auto` on, accepts them automatically (`baseline.auto.accepted=true`).
`merge` and `complete` settled it, but `gate status` still printed `next = ci`, so an agent
reading next would rerun a suite that already settled. An accepted partial is settled for
`next` the same way it is for completion.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G


def _state(repo):
    return fold(EventLog(repo).read_all(), strict=False)


def _auto_accepted_baseline():
    return {
        "red": True,
        "base": "main",
        "ids": ["tests/t.py::test_x"],
        "auto": {"accepted": True, "ids": ["tests/t.py::test_x"]},
    }


def _person_only_baseline():
    return {
        "red": True,
        "base": "main",
        "ids": ["tests/t.py::test_x"],
        "auto": {"accepted": False, "ids": []},
    }


def _project(repo, *, baseline=None):
    run_cli(repo, "init")
    run_cli(repo, "workflow", "pipeline", "task", "implement,ci,merge")
    run_cli(repo, "task", "add", "T1", "--title", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree")
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    run_cli(repo, "gate", "record", "T1", "merge", "--outcome", "passed")
    run_cli(
        repo, "gate", "record", "T1", "ci", "--outcome", "partial", "--reason", "baseline-red: x"
    )
    # what `gate run` measures -- a caller's `gate record` cannot write baseline:
    log = EventLog(repo, "ddflow-gate-run")
    log.append(
        "gate.partial",
        "T1",
        {
            "gate": "ci",
            "by": "ddflow",
            "reason": "baseline-red: tests/t.py::test_x",
            "evidence": {"baseline": baseline or _auto_accepted_baseline()},
        },
    )


def test_next_advances_past_an_auto_accepted_partial(repo):
    _project(repo)
    st = _state(repo)
    cfg = Config.load(repo)
    assert st.items["T1"].gate_outcome("ci") == "partial"  # never a pass
    s = G.status(st, cfg, "T1", G.load_gates(repo, cfg))
    assert s.current != "ci", s.render()
    assert s.complete, s.render()


def test_next_stays_on_a_person_only_partial(repo):
    _project(repo, baseline=_person_only_baseline())
    st = _state(repo)
    cfg = Config.load(repo)
    assert st.items["T1"].gate_outcome("ci") == "partial"
    s = G.status(st, cfg, "T1", G.load_gates(repo, cfg))
    assert s.current == "ci", s.render()
    assert not s.complete, s.render()
