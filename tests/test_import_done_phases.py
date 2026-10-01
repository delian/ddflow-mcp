"""`import --include-done` imports a finished phase as finished (bug B45d5aa72fa).

Measured on the project that cut over with a plain `ddflow import --apply`: the board
said 0 of 301 phases done and 9 of 1131 tasks done. The importer never completed a
PHASE -- only tasks had a completed state -- and a plain import left every ticked box out
without saying where, so "Phase 103: 0/3" read as "nothing shipped" about a phase whose
103.A and 103.C had shipped.

The fixture carries the four shapes that matter: a CLOSED phase with every box ticked (one
declined), a partly-shipped phase like 103 (two ticked, three DEFERRED), a phase with open
work, and a heading-only phase marked done with no box under it. Phase P5 is finished and
open work depends on it, so a plain import brings it in EMPTY -- the case where a re-run
has to complete a phase that is already in the queue.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import ABANDONED, BLOCKED, DONE, OPEN, fold
from ddflow.infra.log import EventLog
from ddflow.services import importer as IM

OK = 0

PLAN = """# Plan

## Phase 1 — Foundations ✅ CLOSED 2026-01-01
- [x] **1.A** — lay the base
- [x] **1.B** — wire the thing
- [ ] **1.C** — DECLINED: not needed after all

## Phase 2 — Hardware — 2.A + 2.C ✅ SHIPPED; 2.B / 2.D / 2.E DEFERRED (operator opted out)
- [x] **2.A** — thin adapter
- [ ] **2.B — kernel paths (DEFERRED)** — hardware-required
- [x] **2.C** — quantization math
- [ ] **2.D — FP8 scaling differences (DEFERRED)** — operator-demand-gated
- [ ] **2.E — offload paths (DEFERRED)** — multi-month scope

## Phase 3 — Live work
- [x] **3.A** — the shipped half
- [ ] **3.B** — the half still to do
  **Needs:** P5

## Phase 4 — Docs ✅ DONE

Written up in the handbook; nothing was ever ticked here.

## P5 — Plumbing
- [x] **P5.A** — pipes
- [x] **P5.B** — valves
"""


def _repo(repo: Path) -> Path:
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "todo.md").write_text(PLAN)
    return repo


def _import(repo: Path, *flags: str) -> dict:
    code, out, err = run_cli(repo, "--json", "import", *flags)
    assert code in (OK, 2), err
    return json.loads(out)


def _state(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False)


def _as_human(repo: Path, kind: str, subject: str, data: dict) -> None:
    EventLog(repo, "operator").append(kind, subject, data)


def _pre_fix_plain_import(repo: Path, monkeypatch) -> None:
    """A plain `import --apply` as importers before B-import-empty-needed-phase wrote it:
    P5 -- finished, and needed by 3.B -- lands EMPTY and OPEN. Logs like that exist, and a
    re-run has to repair them."""
    from ddflow.api.operations import import_project

    monkeypatch.setattr(IM, "_settle_needed_phases", lambda *a, **k: None)
    import_project(repo, apply=True)
    monkeypatch.undo()


def _assert_true_picture(st) -> None:
    items = st.items
    # A CLOSED phase: every box ticked or declined, so the phase is done -- and says why.
    assert items["1"].state == DONE, items["1"].state
    assert "docs/todo.md:3" in items["1"].completion_evidence, items["1"].completion_evidence
    assert items["1.A"].state == DONE and items["1.B"].state == DONE
    assert items["1.C"].state == ABANDONED
    # Partly shipped: the ticked two are present, done and UNDER the phase; the deferred
    # three are held with their reason; the phase stays open.
    assert items["2"].state == OPEN
    for t in ("2.A", "2.C"):
        assert items[t].state == DONE and items[t].parent == "2", (t, items[t])
    for t in ("2.B", "2.D", "2.E"):
        assert items[t].state == BLOCKED, (t, items[t].state)
        assert "DEFERRED" in items[t].blocked_reason, items[t].blocked_reason
    # Open work keeps its phase open.
    assert items["3"].state == OPEN
    assert items["3.A"].state == DONE and items["3.B"].state == OPEN
    # A heading with no box under it is never a phase of open work.
    assert "4" not in items or items["4"].state == DONE
    # Finished, and depended on: done, so the dependent is not stuck behind it.
    assert items["P5"].state == DONE, items["P5"].state
    assert items["P5.A"].state == DONE and items["P5.B"].state == DONE


def test_a_fresh_include_done_import_completes_the_finished_phases(repo):
    _repo(repo)
    run_cli(repo, "init")
    plan = _import(repo, "--include-done")
    phases = {f["id"]: f["done"] for f in plan["found"] if f["kind"] == "phase"}
    assert phases.get("1") is True and phases.get("P5") is True, phases
    assert phases.get("2") is False and phases.get("3") is False, phases

    _import(repo, "--apply", "--include-done")
    _assert_true_picture(_state(repo))


def test_a_plain_import_says_which_ticked_tasks_it_left_out(repo):
    _repo(repo)
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "import")
    assert code == OK, err
    # 1.A, 1.B, 2.A, 2.C, 3.A, P5.A, P5.B: seven ticked boxes, and the phases they are in.
    assert "7 already-ticked task(s)" in out, out
    assert "--include-done" in out, out
    assert "2 (2)" in out and "1 (2)" in out, out

    code, out, err = run_cli(repo, "import", "--apply")
    assert code == OK, err
    assert "7 ticked" in out, f"the apply report must say what it left out:\n{out}"


def test_a_re_run_with_include_done_fills_in_the_plain_import(repo, monkeypatch):
    """The run_nemo_run path: a plain import, then `--include-done` over it."""
    _repo(repo)
    run_cli(repo, "init")
    _pre_fix_plain_import(repo, monkeypatch)
    st = _state(repo)
    assert st.items["P5"].state == OPEN and not st.children("P5"), "fixture: P5 lands empty"
    assert "2.A" not in st.items

    plan = _import(repo, "--include-done")
    completions = {f["id"] for f in plan["found"] if f["kind"] == "completion"}
    assert completions == {"P5"}, plan["found"]

    _import(repo, "--apply", "--include-done")
    _assert_true_picture(_state(repo))

    # Idempotent: a second re-run proposes nothing and writes nothing.
    before = len(EventLog(repo).read_all())
    again = _import(repo, "--include-done")
    assert [f for f in again["found"] if f["kind"] != "branch"] == [], again["found"]
    _import(repo, "--apply", "--include-done")
    assert len(EventLog(repo).read_all()) == before


def test_a_phase_a_human_touched_is_left_alone(repo):
    """Reopened by a person after the import: a re-run must not complete it again."""
    _repo(repo)
    run_cli(repo, "init")
    _import(repo, "--apply")
    # A person blocks and releases phase P5 -- it is OPEN again, and it is theirs now.
    _as_human(repo, "item.blocked", "P5", {"kind": "phase", "reason": "rethinking"})
    _as_human(repo, "item.unblocked", "P5", {"kind": "phase"})
    # A person finishes 3.B in ddflow; the importer must not act on the source's behalf.
    _as_human(repo, "item.completed", "3.B", {"kind": "task", "evidence": "did it"})

    plan = _import(repo, "--include-done")
    assert not [f for f in plan["found"] if f["kind"] == "completion"], plan["found"]
    assert any("P5" in n and "changed" in n for n in plan["notes"]), plan["notes"]
    _import(repo, "--apply", "--include-done")
    st = _state(repo)
    assert st.items["P5"].state == OPEN, "a human-reopened phase was completed by import"
    assert st.items["P5.A"].state == DONE, "its ticked tasks still come in"
    assert st.items["3.B"].state == DONE and st.items["3.B"].completion_evidence == "did it"
    assert st.items["3"].state == OPEN, "a task finished in ddflow closed its phase by import"


def test_a_human_completed_phase_is_not_reopened(repo):
    _repo(repo)
    run_cli(repo, "init")
    _import(repo, "--apply", "--include-done")
    _as_human(repo, "item.completed", "3", {"kind": "phase", "evidence": "operator"})
    _as_human(repo, "item.blocked", "1", {"kind": "phase", "reason": "reopened"})
    _as_human(repo, "item.unblocked", "1", {"kind": "phase"})
    before = len(EventLog(repo).read_all())
    _import(repo, "--apply", "--include-done")
    st = _state(repo)
    assert len(EventLog(repo).read_all()) == before
    assert st.items["3"].state == DONE and st.items["1"].state == OPEN


def test_a_phase_with_no_tasks_completes_only_on_its_heading(repo):
    """The heading rule, on the decision itself: the scanner makes a phase only at its
    first checkbox, so a task-less phase reaches it only as a dependency."""
    for title, done in (("Docs ✅ DONE", True), ("Docs", False)):
        plan = IM.ImportPlan(
            found=[IM.Found(kind="phase", ident="4", title=title, source="docs/todo.md:9")]
        )
        IM._settle_phases(plan, None, IM.Touched(), {})
        assert plan.found[0].done is done, title
        if done:
            assert "docs/todo.md:9" in plan.found[0].extra["evidence"]


def test_every_box_ticked_under_words_that_say_otherwise_stays_open(repo):
    """Measured on the real plan: `## Phase 1 — Tooling (IN PROGRESS)` and
    `**STATUS**: PARTIAL -- 11 of 12 shipped` over nothing but ticked boxes. The rest of
    the work is written in prose; completing the phase would hide it."""
    (repo / "docs").mkdir()
    (repo / "docs" / "todo.md").write_text(
        "# Plan\n\n## Phase 7 — Tooling (IN PROGRESS)\n- [x] **7.A** — one\n- [x] **7.B** — two\n\n"
        "## Phase 8 — Dedupe\n**STATUS**: PARTIAL — 2 of 3 shipped\n\n- [x] **8.A** — a\n"
        "- [x] **8.B** — b\n\n"
        "## Phase 9 — Close the PARTIAL path ✅ SHIPPED\n- [x] **9.A** — done\n- [x] **9.B** — too\n"
    )
    plan = IM.plan_import(repo, None, include_done=True)
    done = {f.ident: f.done for f in plan.found if f.kind == "phase"}
    assert done == {"7": False, "8": False, "9": True}, done
    assert any("7 (its heading says IN PROGRESS)" in n for n in plan.notes), plan.notes


def test_globs_given_to_an_imported_done_task_do_not_make_it_hand_finished(repo, monkeypatch):
    """`--apply` tells the operator to give every task its globs. Doing that is a touch,
    but not of the task's STATE: its done still comes from the source, and its phase is
    still the import's to complete."""
    _repo(repo)
    run_cli(repo, "init")
    _pre_fix_plain_import(repo, monkeypatch)
    # P5 lands empty; a first --include-done run adds P5.A/P5.B -- simulate an older
    # importer that added them but never completed the phase, then someone adds globs.
    log = EventLog(repo, "old-importer")
    for t in ("P5.A", "P5.B"):
        log.append("task.added", t, {"parent": "P5", "title": t, "source": "docs/todo.md:1"})
        log.append("item.completed", t, {"kind": "task", "imported": True, "evidence": "x"})
    _as_human(repo, "task.updated", "P5.A", {"globs": ["src/pipes.py"]})
    plan = _import(repo, "--include-done")
    assert {f["id"] for f in plan["found"] if f["kind"] == "completion"} == {"P5"}, plan


def test_heading_words_are_read_as_words_and_a_negated_done_is_not_done():
    def verdict(title: str) -> bool:
        plan = IM.ImportPlan(
            found=[
                IM.Found(kind="phase", ident="X", title=title, source="docs/todo.md:1"),
                IM.Found(
                    kind="task", ident="X.1", title="t", source="s", done=True, extra={"phase": "X"}
                ),
            ]
        )
        IM._settle_phases(plan, None, IM.Touched(), {})
        return plan.found[0].done

    assert verdict("Wipe the stale cache") is True, "WIP matched inside WIPE"
    assert verdict("Partially landed") is False
    assert verdict("Tooling — NOT DONE") is False
    assert verdict("Tooling — not yet shipped") is False
    assert verdict("Tooling (IN PROGRESS) ✅") is False, "a live word wins over a done marker"
    assert verdict("Close the long-context PARTIAL ✅ SHIPPED") is True


def test_a_phase_somebody_created_by_hand_is_never_completed_by_import(repo):
    """Same id as a source heading, but typed in with `phase add`: no source, not ours."""
    _repo(repo)
    run_cli(repo, "init")
    _as_human(repo, "phase.added", "P5", {"title": "Plumbing, planned by hand"})
    plan = _import(repo, "--include-done")
    assert not [f for f in plan["found"] if f["kind"] == "completion"], plan["found"]
    _import(repo, "--apply", "--include-done")
    st = _state(repo)
    assert st.items["P5"].state == OPEN and st.items["P5.A"].state == DONE
