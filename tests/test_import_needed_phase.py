"""Bug B-import-empty-needed-phase: a plain import left a needed, finished phase OPEN.

A plain `ddflow import --apply` keeps a finished phase that open work depends on (an empty
phase would otherwise be dropped and the dependency become unknown), but its ticked tasks
are left out -- so it landed EMPTY and OPEN, and the dependent was never offered:
`3.B: deps -- phase P5 has no open tasks but is not marked done`. The source's evidence
that P5 is finished (every box under it ticked) is now read on the plain path too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import DONE, OPEN, fold
from ddflow.infra.log import EventLog

PLAN = """# Plan

## Phase 3 — Live work
- [ ] **3.B** — the half still to do
  **Needs:** P5

## P5 — Plumbing
- [x] **P5.A** — pipes
- [x] **P5.B** — valves

## P6 — Half done, and nothing waits on it
- [x] **P6.A** — one
- [ ] **P6.B** — two
"""


def _repo(repo: Path, plan: str = PLAN) -> Path:
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "todo.md").write_text(plan)
    run_cli(repo, "init")
    return repo


def _state(repo: Path):
    return fold(EventLog(repo).read_all())


def test_a_plain_import_completes_the_finished_phase_open_work_needs(repo):
    _repo(repo)
    rc, out, err = run_cli(repo, "import", "--apply")
    assert rc == 0, (out, err)
    st = _state(repo)
    assert st.items["P5"].state == DONE, st.items["P5"].state
    assert "every task under it" in st.items["P5"].completion_evidence, st.items["P5"]
    rc, out, _ = run_cli(repo, "--json", "next")
    assert rc == 0, out
    assert "3.B" in json.dumps(json.loads(out)["ready"]), out


def test_the_plain_import_still_leaves_the_ticked_tasks_out(repo):
    """The phase is completed on the evidence of its ticked boxes; the boxes themselves
    stay out, as a plain import promises (--include-done brings them in)."""
    _repo(repo)
    run_cli(repo, "import", "--apply")
    st = _state(repo)
    assert "P5.A" not in st.items and "P5.B" not in st.items
    assert st.items["P6"].state == OPEN


def test_a_needed_phase_with_work_left_under_it_stays_open(repo):
    _repo(
        repo,
        PLAN.replace("- [x] **P5.B** — valves", "- [ ] **P5.B** — valves"),
    )
    run_cli(repo, "import", "--apply")
    assert _state(repo).items["P5"].state == OPEN


def test_a_re_run_completes_a_phase_an_earlier_plain_import_left_empty(repo, monkeypatch):
    """A log imported before the fix holds P5 empty and open with 3.B stuck behind it; a
    plain re-run must complete P5, so 3.B is released."""
    from ddflow.api.operations import import_project
    from ddflow.services import importer as IM

    _repo(repo)
    monkeypatch.setattr(IM, "_settle_needed_phases", lambda *a, **k: None, raising=False)
    import_project(repo, apply=True)
    monkeypatch.undo()
    st = _state(repo)
    assert st.items["P5"].state == OPEN and st.items["3.B"].state == OPEN, "fixture: stuck"
    rc, out, _ = run_cli(repo, "--json", "next")
    assert "3.B" not in json.dumps(json.loads(out).get("ready", [])), out

    rc, out, err = run_cli(repo, "import", "--apply")
    assert rc in (0, 2), (out, err)
    assert _state(repo).items["P5"].state == DONE
    rc, out, _ = run_cli(repo, "--json", "next")
    assert rc == 0 and "3.B" in json.dumps(json.loads(out)["ready"]), out


def test_a_needed_phase_a_person_reopened_is_left_alone_by_a_plain_re_run(repo, monkeypatch):
    from ddflow.api.operations import import_project
    from ddflow.services import importer as IM

    _repo(repo)
    monkeypatch.setattr(IM, "_settle_needed_phases", lambda *a, **k: None, raising=False)
    import_project(repo, apply=True)
    monkeypatch.undo()
    EventLog(repo, "operator").append("item.blocked", "P5", {"kind": "phase", "reason": "x"})
    EventLog(repo, "operator").append("item.unblocked", "P5", {"kind": "phase"})
    run_cli(repo, "import", "--apply")
    assert _state(repo).items["P5"].state == OPEN
