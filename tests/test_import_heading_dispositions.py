"""What a heading says about ITSELF outranks what its ancestors and children imply.

Found importing home-simulator (2026-09-29), whose `docs/todo.md` holds every shape
below. Each lost or misfiled real open work while the import reported success:

* `## Phase 40 — Phase 34 follow-ups`, tasks `34.6e`, `34.8f`: the child prefix renamed
  the phase to `34` -- the id of a different, CLOSED phase of the same file;
* `### Phase 39 follow-ups (not started)` under a `## Phase 38` whose STATUS is SHIPPED:
  38.9 and 38.10 were dropped as history, although the heading says they are not started;
* `### P42.8 — Future work (deliberately not in this phase)` under a SHIPPED section:
  its open boxes were dropped as history rather than held as not-yet work;
* and the note reporting disposed items gave a count only, so none of it was visible.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import importer as IM


def _write(repo: Path, text: str) -> None:
    p = repo / "docs" / "todo.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _plan(repo: Path) -> IM.ImportPlan:
    return IM.plan_import(repo, None, max_tasks=10**6)


def _by_id(plan: IM.ImportPlan, kind: str) -> dict[str, IM.Found]:
    return {f.ident: f for f in plan.found if f.kind == kind}


PHASE_NUMBERED = """\
## Phase 34 — Ecosystem expansion ✅ CLOSED
**STATUS**: CLOSED

- [x] **34.6** — credentials
- [x] **34.8** — scene remotes

## Phase 40 — Phase 34 follow-ups (deferred scope identified while closing Phase 34)
**STATUS**: IN PROGRESS — two items remain.

- [x] **34.5f** SHIPPED.
- [ ] **34.6e** multi-credential locks.
- [ ] **34.8f** scene_remote as a trigger source.

## Phase 21 — Egocentric reference resolution
- [ ] **P21.7** humanizer deixis register.
- [ ] **P21.8** runner speaker parity.
"""


def test_a_heading_that_names_its_phase_number_is_not_renamed_to_a_different_one(repo):
    _write(repo, PHASE_NUMBERED)
    plan = _plan(repo)
    phases = _by_id(plan, "phase")
    assert "34" not in phases, (
        "Phase 40 was renamed to its children's prefix -- the id of the closed Phase 34"
    )
    assert "40" in phases
    tasks = _by_id(plan, "task")
    assert tasks["34.6e"].extra["phase"] == "40"
    assert tasks["34.8f"].extra["phase"] == "40"


def test_a_child_prefix_that_AGREES_with_the_heading_number_is_still_adopted(repo):
    """`## Phase 21` with `P21.7`: the project writes `P21`, and that is the same number."""
    _write(repo, PHASE_NUMBERED)
    plan = _plan(repo)
    assert "P21" in _by_id(plan, "phase")
    assert _by_id(plan, "task")["P21.7"].extra["phase"] == "P21"


LIVE_UNDER_SHIPPED = """\
## Phase 38 — Device packs ✅ SHIPPED
**STATUS**: SHIPPED — see commit `479fe91`

- [ ] **38.7** — a leftover box the shipped status disposes of

### Phase 39 follow-ups (not started)
- [ ] **38.9 Free-text arg vocabulary for the actuate goal.** skips non-enum args
- [ ] **38.10 Cross-device pack scenarios.** broad but shallow

### P42.8 — Future work (deliberately not in this phase)
- [ ] Give base room-blueprint devices transports so gateway fan-outs exceed 3-5 nodes
- [x] Multi-gateway homes. ✅ Closed by P41.6
"""


def test_a_sub_heading_that_says_not_started_is_live_under_a_shipped_section(repo):
    _write(repo, LIVE_UNDER_SHIPPED)
    tasks = _by_id(_plan(repo), "task")
    assert "38.9" in tasks and "38.10" in tasks, "open work under 'not started' was dropped"
    assert tasks["38.9"].extra["disposition"] == ""
    assert tasks["38.10"].extra["disposition"] == ""


def test_the_shipped_sections_own_open_box_is_still_disposed(repo):
    """The override is the sub-heading's, not a general amnesty for shipped sections."""
    _write(repo, LIVE_UNDER_SHIPPED)
    assert "38.7" not in _by_id(_plan(repo), "task")


def test_future_work_is_held_not_dropped(repo):
    _write(repo, LIVE_UNDER_SHIPPED)
    tasks = _by_id(_plan(repo), "task")
    held = [t for t in tasks.values() if "room-blueprint" in t.title]
    assert held, "open future work under a shipped section was dropped as history"
    assert held[0].extra["disposition"] == "hold"
    assert "FUTURE WORK" in held[0].extra["disposition_why"]


def test_future_work_under_a_live_section_is_held_not_offered(repo):
    _write(repo, "## S\n\n- [ ] **S.1** — now\n\n### Future work\n- [ ] **S.9** — later\n")
    tasks = _by_id(_plan(repo), "task")
    assert tasks["S.1"].extra["disposition"] == ""
    assert tasks["S.9"].extra["disposition"] == "hold"


def test_the_note_names_the_open_work_it_did_not_import(repo):
    _write(repo, LIVE_UNDER_SHIPPED)
    notes = " ".join(_plan(repo).notes)
    assert "NOT imported because the source disposes" in notes
    assert "38.7" in notes, "a count alone hides which open work was dropped"


def test_a_heading_ABOUT_a_phase_does_not_number_its_own_section(repo):
    """`### Phase 39 follow-ups` names Phase 39 without being it; its items `38.9` and
    `38.10` keep the prefix the project itself uses for them."""
    _write(repo, "## Plan\n\n### Phase 39 follow-ups\n- [ ] **38.9** — a\n- [ ] **38.10** — b\n")
    plan = _plan(repo)
    assert "38" in _by_id(plan, "phase")
    assert "39" not in _by_id(plan, "phase")


def test_a_marker_word_inside_the_TITLE_is_not_a_disposition(repo):
    """`**C11** consumable-triggered chains (inventory → todo → deferred notify)`: the bold
    is only the id, so the title follows it, and "deferred notify" is what the chain does.
    Held as DEFERRED, the item the project's own handoff says to start with was never
    offered."""
    _write(
        repo,
        "## S\n\n"
        "- [ ] **C11** consumable-triggered chains (inventory -> todo -> deferred notify) - 34.1\n"
        "- [ ] **C12** the retry path for skipped batches. DECLINED: not needed\n"
        "- [ ] **C13** (deferred) multi-credential locks\n"
        "- [ ] **P21.7 (MED, deferred from P21.4) - Humanizer deixis register**\n",
    )
    tasks = _by_id(_plan(repo), "task")
    assert tasks["C11"].extra["disposition"] == "", "title prose read as a deferral"
    assert "C12" not in tasks, "a disposition AFTER the title still closes the item"
    assert tasks["C13"].extra["disposition"] == "hold", "an aside that IS the verdict holds"
    assert tasks["P21.7"].extra["disposition"] == "hold", "a verdict clause inside the aside"
