"""Abandoning and removing work — the exits from the queue that did not exist.

Found by the end-to-end orchestration scenario: a task created speculatively and then
not wanted held its phase open FOREVER, because `complete` counts any task that is not
`done` as unfinished and nothing could ever finish it. The event kinds `item.abandoned`,
`task.removed` and `phase.removed` were all declared and handled, and nothing emitted
any of them — a vocabulary with no way to say the words.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import finish, pass_pipeline, run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _queue(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "a/*")
    run_cli(repo, "task", "add", "P1.T2", "--phase", "P1", "--globs", "b/*")
    return repo


def test_an_abandoned_task_stops_holding_its_phase_open(repo):
    _queue(repo)
    run_cli(repo, "claim", "P1.T1", "--no-worktree")
    assert finish(repo, "P1.T1")[0] == OK

    pass_pipeline(repo, "P1")
    code, _, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == REFUSED and "P1.T2" in err, err

    assert run_cli(repo, "abandon", "P1.T2", "--reason", "not needed")[0] == OK
    code, _out, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == OK, f"the phase still will not close:\n{err}"


def test_abandon_requires_a_reason_and_records_it(repo):
    _queue(repo)
    code, _out2, _err2 = run_cli(repo, "abandon", "P1.T2")
    assert code != OK, "an unexplained abandonment is invisible later"
    run_cli(repo, "abandon", "P1.T2", "--reason", "superseded by P1.T1")
    log = "\n".join(p.read_text() for p in (repo / ".orchard" / "events").glob("*.jsonl"))
    assert "superseded by P1.T1" in log


def test_an_abandoned_item_is_not_offered_again(repo):
    _queue(repo)
    run_cli(repo, "abandon", "P1.T2", "--reason", "dropped")
    data = json.loads(run_cli(repo, "--json", "next", "--phase", "P1")[1])
    assert "P1.T2" not in [r["id"] for r in data["ready"]]
    assert "P1.T2" not in [b["item"] for b in data["blocked"]]


def test_abandoning_releases_the_lease(repo):
    _queue(repo)
    run_cli(repo, "claim", "P1.T2", "--no-worktree")
    run_cli(repo, "abandon", "P1.T2", "--reason", "dropped")
    shown = json.loads(run_cli(repo, "--json", "show", "P1.T2")[1])
    assert shown["lease"] is None, "an abandoned item still holds its lease"


def test_removing_an_item_with_dependents_is_refused(repo):
    _queue(repo)
    run_cli(repo, "task", "add", "P1.T3", "--phase", "P1", "--needs", "P1.T1")
    code, _, err = run_cli(repo, "remove", "P1.T1")
    assert code == REFUSED
    assert "P1.T3" in err and "blocked on something that no longer exists" in err


def test_removing_a_phase_with_tasks_is_refused(repo):
    _queue(repo)
    code, _, err = run_cli(repo, "remove", "P1")
    assert code == REFUSED and "still has 2 task(s)" in err


def test_removal_is_recorded_not_erased(repo):
    """The log is append-only; the history of dropped work must survive."""
    _queue(repo)
    run_cli(repo, "task", "add", "P1.T9", "--phase", "P1", "--title", "speculative")
    assert run_cli(repo, "remove", "P1.T9", "--reason", "not needed after all")[0] == OK
    data = json.loads(run_cli(repo, "--json", "next", "--phase", "P1")[1])
    assert "P1.T9" not in [r["id"] for r in data["ready"]]
    log = "\n".join(p.read_text() for p in (repo / ".orchard" / "events").glob("*.jsonl"))
    assert "task.removed" in log and "P1.T9" in log
    assert "not needed after all" in log


def test_every_declared_event_kind_can_actually_be_emitted():
    """A vocabulary with no way to say the words.

    `item.abandoned`, `task.removed` and `phase.removed` were declared in the handler
    registry and handled by the fold, and no command emitted any of them — so three
    legitimate states of a work item were unreachable. `log.compacted` remains
    deliberately unemitted and is allowlisted with its reason.
    """
    import pathlib as _p

    from orchard.model import HANDLERS

    src = "".join(
        _p.Path(f"orchard/{n}.py").read_text()
        for n in ("cli", "lease", "gates", "session", "store", "worktree", "enforce")
    )
    allowed_unemitted = {
        # Reserved for a compaction pass that is not implemented; filed as B6.
        "log.compacted",
    }
    dead = [
        k
        for k in sorted(HANDLERS)
        if k not in allowed_unemitted and not k.startswith("gate.") and f'"{k}"' not in src
    ]
    assert not dead, f"event kinds nothing can emit: {dead}"


def test_the_board_does_not_count_abandoned_tasks_as_outstanding(repo):
    """A completed phase rendered as "3/4 tasks" — which reads as unfinished work that
    no longer exists. Abandoned is settled, like done."""
    _queue(repo)
    run_cli(repo, "task", "add", "P1.T3", "--phase", "P1", "--globs", "c/*")
    run_cli(repo, "abandon", "P1.T3", "--reason", "dropped")
    board = run_cli(repo, "board")[1]
    assert "0/2 tasks" in board, board[:400]
    assert "1 abandoned" in board, "the abandoned task should still be visible, as such"
    row = next(ln for ln in board.splitlines() if "**P1.T3**" in ln)
    assert row.strip().startswith("| [-]"), f"abandoned should mark as [-]: {row}"
