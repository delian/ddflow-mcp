"""`ddflow phase close <id> --imported`: close a finished phase whose work predates ddflow.

bug `import-leaves-finished-phases-open`: `ddflow brief` nagged "finished but still open"
about phases whose every task is done, and the only way out was `complete --force`, which
records nothing about WHY no gate ran. The importer already writes exactly the right event
for work it closes on the way in; this is the same door for a phase already in the queue.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state as _state

from ddflow.core.model import DONE, OPEN
from ddflow.infra.log import EventLog

PLAN = """# Plan

## Phase 3 — Live work
- [x] **3.A** — the shipped half
- [ ] **3.B** — the half still to do
"""


def _repo(repo: Path) -> Path:
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "todo.md").write_text(PLAN)
    run_cli(repo, "init")
    return repo


def _as_human(repo: Path, kind: str, subject: str, data: dict) -> None:
    EventLog(repo, "operator").append(kind, subject, data)


def _finished_imported_phase(repo: Path) -> Path:
    """An imported phase whose every task is now done -- the case the nag fires on."""
    _repo(repo)
    assert run_cli(repo, "import", "--apply")[0] in (0, 2)
    _as_human(repo, "item.completed", "3.B", {"kind": "task", "evidence": "did it"})
    return repo


def test_it_closes_a_finished_imported_phase_and_the_nag_stops(repo):
    _finished_imported_phase(repo)
    st = _state(repo)
    assert st.items["3"].state == OPEN, "fixture: the phase is open"
    assert all(t.terminal for t in st.tasks("3")), "fixture: every task is finished"
    assert "finished but still open" in run_cli(repo, "brief")[1], "fixture: the nag fires"

    code, out, err = run_cli(repo, "phase", "close", "3", "--imported")
    assert code == 0, (out, err)

    st = _state(repo)
    assert st.items["3"].state == DONE, st.items["3"].state
    # The evidence is the importer's own: why no gate ran, and where the work came from.
    assert "completed before ddflow" in st.items["3"].completion_evidence
    assert "docs/todo.md" in st.items["3"].completion_evidence
    assert "finished but still open" not in run_cli(repo, "brief")[1], "the nag is gone"
    # The work under it is untouched: this closes the PHASE, it rewrites no task.
    assert st.items["3.B"].state == DONE


def test_it_refuses_a_phase_that_still_has_open_work(repo):
    _repo(repo)
    run_cli(repo, "import", "--apply")

    code, out, err = run_cli(repo, "phase", "close", "3", "--imported")

    assert code == 3, (out, err)
    assert "still open" in out + err, out + err
    assert _state(repo).items["3"].state == OPEN, "a refusal wrote something"


def test_it_refuses_without_the_imported_flag(repo):
    """The flag IS the authority: without it there is no claim that this work predates us."""
    _finished_imported_phase(repo)

    code, out, err = run_cli(repo, "phase", "close", "3")

    assert code == 3, (out, err)
    assert "--imported" in out + err, (out, err)
    assert _state(repo).items["3"].state == OPEN, "a refusal wrote something"


def test_it_refuses_a_phase_the_importer_did_not_create(repo):
    _repo(repo)
    _as_human(repo, "phase.added", "P9", {"title": "planned by hand"})
    _as_human(repo, "task.added", "P9.T1", {"parent": "P9", "title": "t"})
    _as_human(repo, "item.completed", "P9.T1", {"kind": "task", "evidence": "done"})

    code, out, err = run_cli(repo, "phase", "close", "P9", "--imported")

    assert code == 3, (out, err)
    assert "not created by `ddflow import`" in out + err, out + err
    assert _state(repo).items["P9"].state == OPEN


def test_it_refuses_a_task_and_an_unknown_id(repo):
    _finished_imported_phase(repo)

    code, out, err = run_cli(repo, "phase", "close", "3.B", "--imported")
    assert code == 3 and "not a phase" in out + err, (out, err)

    code, _out, _err = run_cli(repo, "phase", "close", "nope", "--imported")
    assert code == 1, "an unknown id is a failure, not a refusal"


def test_it_refuses_a_phase_that_is_already_closed(repo):
    _finished_imported_phase(repo)
    assert run_cli(repo, "phase", "close", "3", "--imported")[0] == 0

    code, out, err = run_cli(repo, "phase", "close", "3", "--imported")

    assert code == 3 and "already done" in out + err, (out, err)


def test_the_mcp_tool_closes_it_too(repo):
    """Both surfaces, one implementation (the declaration generates the tool)."""
    from ddflow.surfaces.mcp import Server

    _finished_imported_phase(repo)

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_phase_close",
                "arguments": {"id": "3", "imported": True},
            },
        }
    )

    assert not reply["result"].get("isError"), reply
    assert _state(repo).items["3"].state == DONE
