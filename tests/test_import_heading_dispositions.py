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


# -- the rubber-duck's refutations of the first version -------------------------------------


def test_a_live_sub_heading_does_not_override_a_DECLINED_or_DEFERRED_heading(repo):
    """Only a STATUS verdict (SHIPPED over a section) is overridden. A heading that says
    the section is declined or deferred is the operator's word about everything in it."""
    _write(
        repo,
        "## Declined ideas\n### Idea A (not started)\n- [ ] **A.1** x\n\n"
        "## Deferred\n### Later (in progress)\n- [ ] **B.1** y\n",
    )
    plan = _plan(repo)
    tasks = _by_id(plan, "task")
    assert "A.1" not in tasks, "declined work offered because a sub-heading says not started"
    assert tasks["B.1"].extra["disposition"] == "hold"


def test_a_verdict_after_a_colon_in_an_aside_still_disposes(repo):
    _write(
        repo,
        "## S\n\n- [ ] **X.1** (operator decision after review: DECLINED)\n"
        "- [ ] **X.2** (decided 2026-09-01: declined)\n- [ ] **X.3** (todo -> deferred notify)\n",
    )
    tasks = _by_id(_plan(repo), "task")
    assert "X.1" not in tasks and "X.2" not in tasks, "a declined item was offered"
    assert tasks["X.3"].extra["disposition"] == "", "a short prose aside is not a verdict"


def test_dotted_prose_in_a_bold_title_is_not_an_id(repo):
    _write(
        repo,
        "## S\n\n- [ ] **e.g. add caching** later\n- [ ] **README.md rewrite** - docs\n"
        "- [ ] **Node.js migration**\n",
    )
    ids = set(_by_id(_plan(repo), "task"))
    assert not ids & {"e.g.", "README.md", "Node.js"}, ids


def test_a_prefix_spelling_the_heading_number_is_adopted(repo):
    _write(
        repo,
        "## Phase 4 - a\n- [ ] **T4.1** x\n- [ ] **T4.2** y\n\n"
        "## Session 12 - b\n- [ ] **S12.1** x\n- [ ] **S12.2** y\n\n"
        "## Phase 21 - c\n- [ ] **21A.1** x\n- [ ] **21A.2** y\n",
    )
    phases = set(_by_id(_plan(repo), "phase"))
    assert {"T4", "S12", "21A"} <= phases, phases


def test_a_heading_that_only_MENTIONS_future_work_does_not_hold(repo):
    _write(repo, "## Phase 5 — Future work planning\n- [ ] **5.1** plan it\n")
    assert _by_id(_plan(repo), "task")["5.1"].extra["disposition"] == ""


def test_natural_language_verdicts_in_an_aside_still_dispose(repo):
    """A CLOSED word anywhere in an aside disposes, as it always did; a HOLD word must
    lead its clause, allowing filler ('marked deferred') -- `deferred notify` is prose."""
    _write(
        repo,
        "## S\n\n- [ ] **X.1** (operator declined)\n- [ ] **X.2** (now superseded by X.9)\n"
        "- [ ] **X.3** (was refuted in review)\n- [ ] **X.4** (marked deferred)\n"
        "- [ ] **X.5** (explicitly out of scope)\n- [ ] **X.8** (decision: operator DECLINED)\n",
    )
    tasks = _by_id(_plan(repo), "task")
    assert not {"X.1", "X.2", "X.3", "X.5", "X.8"} & set(tasks), sorted(tasks)
    assert tasks["X.4"].extra["disposition"] == "hold"


def test_a_prefix_with_no_number_is_the_projects_name_for_the_phase(repo):
    _write(
        repo,
        "## Phase 3 — B track\n- [ ] **B.1** x\n- [ ] **B.2** y\n\n"
        "## Session 7 — DRIVERFIX defects\n- [ ] **DRIVERFIX.1** x\n- [ ] **DRIVERFIX.2** y\n",
    )
    phases = set(_by_id(_plan(repo), "phase"))
    assert {"B", "DRIVERFIX"} <= phases, phases


def test_a_numbered_future_work_heading_holds(repo):
    _write(
        repo, "## Phase 14 — Future work\n- [ ] **14.1** a\n\n### 4. Future work\n- [ ] **4.9** b\n"
    )
    tasks = _by_id(_plan(repo), "task")
    assert tasks["14.1"].extra["disposition"] == "hold"
    assert tasks["4.9"].extra["disposition"] == "hold"


def test_future_work_is_the_title_not_a_compound_and_not_over_a_live_verdict(repo):
    _write(
        repo,
        "## S\n### Future work-related cleanup\n- [ ] **A.2** z\n\n"
        "### Phase 39 — Future work (IN PROGRESS)\n- [ ] **A.3** y\n",
    )
    tasks = _by_id(_plan(repo), "task")
    assert tasks["A.2"].extra["disposition"] == ""
    assert tasks["A.3"].extra["disposition"] == ""


def test_an_en_dash_separates_like_an_em_dash(repo):
    _write(
        repo,
        "## Phase 34 \u2013 done ✅ CLOSED\n**STATUS**: CLOSED\n- [x] **34.1** x\n\n"
        "## Phase 40 \u2013 Phase 34 follow-ups\n- [ ] **34.6e** a\n- [ ] **34.8f** b\n\n"
        "## Phase 14 \u2013 Future work\n- [ ] **14.1** c\n",
    )
    plan = _plan(repo)
    assert "40" in _by_id(plan, "phase") and "34" not in _by_id(plan, "phase")
    assert _by_id(plan, "task")["14.1"].extra["disposition"] == "hold"


def test_a_zero_numbered_prefix_is_a_number_not_a_name(repo):
    _write(repo, "## Phase 5 — x\n- [ ] **P0.1** a\n- [ ] **P0.2** b\n")
    assert "P0" not in _by_id(_plan(repo), "phase")


def test_title_prose_after_an_id_only_bold_is_not_a_verdict(repo):
    """`**C14** add deferred notifications to the chain`: no separator after the id, so
    the words are the title. A verdict after it still counts."""
    long_aside = "(inventory " + "-> step " * 25 + "-> deferred notify)"
    _write(
        repo,
        "## S\n\n- [ ] **C14** add deferred notifications to the chain\n"
        f"- [ ] **C15** chains {long_aside} - 34.1\n"
        "- [ ] **C16** retry skipped batches. DEFERRED: waits for the vendor\n",
    )
    tasks = _by_id(_plan(repo), "task")
    assert tasks["C14"].extra["disposition"] == ""
    assert tasks["C15"].extra["disposition"] == "", "a truncated aside read as a verdict"
    assert tasks["C16"].extra["disposition"] == "hold"
