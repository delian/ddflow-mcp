"""The import, run against the shapes a REAL long-lived project actually writes.

`tests/test_import.py` covers the contract on a fixture written to be parsed. This file
covers what happened when the same code met four hundred days of a real repository:
4,799 checkboxes, 14,362 lines of lessons, 2,561 journal sections, an OptMem store and
47 declared dependencies. Every defect below was found that way and none of them was
visible in a hand-written fixture, because a hand-written fixture is written by the
same person who wrote the parser.

The fixture here is a MINIATURE of those shapes, so the regressions run everywhere.
The last test runs against the real repository when it happens to be on this machine,
and skips otherwise -- it is a canary, never the proof.
"""

from __future__ import annotations

import collections
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from orchard.services import importer as IM

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

# -- the miniature -------------------------------------------------------------------

#: Every id shape and every annotation shape found in the real todo files.
TODO = """# Roadmap

### 142.A — the scaling-law advisor is wrong (P0; CONFIRMED)

- [x] **142.1** — the probe
- [x] **142.2** — the fix

### 143.I — proxy search, blocked

- [ ] **143.37** — Depends on the budget arithmetic
  **Needs:** `142.A` and 999.MISSING
  **Globs:** src/advisor.py

### 160.A — the draft scorer

- [ ] **160.A.1** — score a draft without running it
- [ ] **160.A.2** — calibrate it

### 160.B — variant zero

- [ ] **160.B.1** — is a fine-tune needed at all?
- [ ] **160.B.2** — measure it

## Session DRIVERFIX — the defects the live runs exposed

- [ ] **DRIVERFIX.1 — step 1 picks operator-DEFERRED work.** The picker reads the
- [ ] **DRIVERFIX.2 (BLOCKED) — `phase.py` read the wrong todo.**
- [ ] a task nobody gave an id

### 150.Z — finished long ago, nothing depends on it

- [x] **150.Z.1** — done
- [x] **150.Z.2** — also done

## Phase 99 — depth-recurrent — **SHIPPED 2026-06-01**

- [ ] **99.1** — the last loose end nobody ticked
"""

LESSONS = """# Lessons

## A duplicate title, truncated the same way in its first thirty-two characters
First one.

## A duplicate title, truncated the same way but ending differently
Second one.
"""

RESEARCH = """# Research

## SOAP versus Muon on this box

Mechanism, falsifier, decisive test. CONFIRMED by the probe below.

### Sources (opened, not snippet-cited)

arXiv:2409.11321.

### Known gaps

No 8-GPU arm.

## Pipeline parallelism beyond FSDP

REFUTED: the envelope arithmetic does not close.
"""

JOURNAL = """# 2026-04

## Phase 98 closure — Engram conditional memory (2026-04-30)

It shipped.

## 2026-04-22 — Phase 71: autotune gains

The trigger was 60-90% GPU utilisation.
"""

ADR = "# Use Postgres\n\n## Status\nAccepted\n\n## Decision\nConcurrent writers.\n"
ADR_README = "# Architecture Decision Records\n\n## Index\n\n- 0001 use postgres\n"

#: OptMem's fixed-width one-record-per-line store, padded exactly as the tool pads it.
OPTMEM = "\n".join(
    f"#{n} {date} {text}".ljust(317)
    for n, date, text in (
        (0, "2026-07-31", "Hardware: 8x H200 GPUs on this box, usually idle."),
        (1, "2026-07-31", "Full suite: use -n 16, NOT -n auto; auto is 2.3x slower."),
        (2, "2026-09-21", "critic_review can exit 0 having DEGENERATED. Exit 0 is not a pass."),
    )
)


def _real(repo: Path) -> Path:
    """A repository shaped like a real one, at 1/1000 scale."""
    (repo / "docs" / "adr").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "log").mkdir(parents=True, exist_ok=True)
    (repo / ".agent_memory").mkdir(exist_ok=True)
    (repo / "docs" / "todo.md").write_text(TODO)
    (repo / "docs" / "lessons.md").write_text(LESSONS)
    (repo / "docs" / "RESEARCH.md").write_text(RESEARCH)
    (repo / "docs" / "log" / "2026-04.md").write_text(JOURNAL)
    (repo / "docs" / "adr" / "0001-postgres.md").write_text(ADR)
    (repo / "docs" / "adr" / "README.md").write_text(ADR_README)
    (repo / ".agent_memory" / "LOG.txt").write_text(OPTMEM + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "history"], check=True, capture_output=True
    )
    return repo


@pytest.fixture
def plan(repo: Path):
    _real(repo)
    return IM.plan_import(repo, None, max_tasks=500)


def _state(repo: Path):
    from orchard.core.model import fold
    from orchard.infra.log import EventLog

    return fold(EventLog(repo, "agent-test").read_all(), strict=False)


def _by_id(plan) -> dict[str, IM.Found]:
    return {f.ident: f for f in plan.found}


# -- ids: the project's own, not ours -------------------------------------------------


def test_a_numeric_dotted_phase_id_is_read_not_slugged(plan):
    """`### 142.A` is not a guess — it is the id in every commit trailer for months.

    The id pattern required a LEADING LETTER, so `142.A` was not an id at all and the
    phase imported as `142A-THE-SCALING-LAW-ADV`. On the real repository that broke 39
    of the 47 declared dependencies at once: they pointed at `142.A`, which then
    existed nowhere, and an unknown dependency is treated as unmet by design.
    """
    ids = _by_id(plan)
    assert "142.A" in ids and ids["142.A"].kind == "phase", sorted(ids)
    assert "160.A" in ids and "160.B" in ids, sorted(ids)
    assert not any(i.startswith("142A-") for i in ids), sorted(ids)


def test_the_heading_id_outranks_the_child_prefix(plan):
    """`### 142.A` has children `142.1`, `142.2`, so the child prefix is `142`.

    Taking it renames the phase out from under every `Needs: 142.A` in the file — the
    rename was built to RECOVER ids and would have destroyed this one.
    """
    assert _by_id(plan)["142.A"].kind == "phase"


def test_an_id_inside_a_spanning_bold_is_still_an_id(plan):
    """`- [ ] **DRIVERFIX.1 — step 1 picks ...**` — the bold wraps the id AND the prose.

    Neither the delimited pattern (which needs `**` straight after the id) nor the bare
    one (anchored at `^`, blocked by the `**`) matched, so every one of these imported
    under a slug derived from its own prose.
    """
    ids = _by_id(plan)
    assert "DRIVERFIX.1" in ids, sorted(i for i in ids if "DRIVER" in i.upper())
    assert "DRIVERFIX.2" in ids, "…and with a `(BLOCKED)` annotation before the dash"
    assert "**" not in ids["DRIVERFIX.1"].title, ids["DRIVERFIX.1"].title


def test_a_checkbox_with_no_id_does_not_get_one_invented(plan):
    """The rule cuts both ways: a permissive id pattern eats the first two words of a
    title. `3 things to do` must not import as item `3`."""
    derived = [f for f in plan.found if f.kind == "task" and "nobody gave an id" in f.title]
    assert len(derived) == 1
    assert derived[0].ident not in ("a", "task"), derived[0].ident


def test_no_two_proposed_items_share_an_id(plan):
    """A collision is SILENT data loss: the second event folds over the first, one item
    disappears, and the import reports both as written.

    Two lessons whose titles agree in their first 32 characters is all it takes; the
    real corpus had 42 such pairs.
    """
    counts = collections.Counter(f.ident for f in plan.found)
    assert [i for i, n in counts.items() if n > 1] == []
    assert len([f for f in plan.found if f.kind == "lesson"]) == 2, "and both survive"


# -- dependencies: the thing the ids exist for ----------------------------------------


def test_one_file_reachable_by_two_patterns_is_scanned_once(repo):
    """`TODO.md` and `todo.md` are two patterns and one file on a case-insensitive
    filesystem; a symlinked `docs/` is the same story.

    Concatenating the glob results scanned it twice and proposed every item twice --
    which the id uniquifier then dutifully renamed to `-2` rather than dropping, so the
    queue got a full duplicate of the file under ids nobody would recognise.
    """
    _real(repo)
    (repo / "TODO.md").symlink_to(repo / "docs" / "todo.md")
    plan = IM.plan_import(repo, None, max_tasks=500)
    # The lessons file has a genuine pair of colliding titles, so `-2` on its own is
    # expected; what must not appear is a second copy of a todo item that has a
    # perfectly good unique id of its own.
    from_todo = [f.ident for f in plan.found if f.kind in ("task", "phase")]
    assert [i for i in from_todo if i.endswith("-2")] == [], (
        f"the same file was read twice and the second copy renamed: {from_todo}"
    )
    assert from_todo.count("160.A.1") == 1


def test_every_declared_dependency_resolves_to_something_imported(plan):
    """The end-to-end statement, and the one that actually matters.

    An unknown dependency is treated as unmet deliberately, so a typo surfaces as
    blocked work rather than as work that starts early. That design turns every id
    mismatch into permanently stuck work — 39 of 47 on the real repository — while the
    import reports success.
    """
    ids = set(_by_id(plan))
    unresolved = {d for f in plan.found for d in f.needs} - ids
    # `999.MISSING` is in the fixture on purpose: a dependency on an id that really is
    # not there must stay unmet, so a typo surfaces as blocked work rather than as work
    # that starts early. Everything else must resolve.
    assert unresolved == {"999.MISSING"}, f"imported blocked on ids that do not exist: {unresolved}"


def test_a_dependency_on_an_id_that_is_nowhere_is_REPORTED(plan):
    """It is kept and left unmet — but silently, the item is simply never offered,
    which looks exactly like an item nobody has got to yet."""
    assert any("999.MISSING" in n for n in plan.notes), plan.notes
    assert any("UNMET" in n for n in plan.notes), plan.notes


def test_a_needs_line_is_parsed_as_ids_not_as_words(plan):
    """`**Needs:** `142.A` and 999.MISSING` — splitting on commas and keeping the
    fragments produced dependencies on ``and`` and on a backticked id."""
    needs = _by_id(plan)["143.37"].needs
    assert "142.A" in needs, needs
    assert "and" not in needs, "a separator word is not an id"
    assert all("`" not in n for n in needs), needs


def test_a_finished_phase_that_open_work_needs_is_kept(plan):
    """`142.A` is finished — every box under it is ticked — and `143.37` needs it.

    Dropped as an empty container, the open task imports blocked on an id the queue has
    never heard of. The same carve-out already existed for finished TASKS and was
    missing for phases.
    """
    assert "142.A" in _by_id(plan)


# -- sections: one entry, not five fragments ------------------------------------------


def test_sub_headings_stay_inside_their_entry(plan):
    """A research entry is a `##` with `###` parts under it — "Sources", "Known gaps".

    Splitting on every `#{2,6}` shredded one entry into five, four of which were
    titled "Sources (opened, not snippet-cited)" and meant nothing alone. Measured on
    the real file: 358 fragments where there are about 90 entries.
    """
    research = [f for f in plan.found if f.kind == "research"]
    assert len(research) == 2, [f.title for f in research]
    soap = next(f for f in research if "SOAP" in f.title)
    assert "arXiv:2409.11321" in soap.body, "the sub-section is body, not a sibling"
    assert soap.extra["verdict"] == "CONFIRMED"
    assert next(f for f in research if "Pipeline" in f.title).extra["verdict"] == "REFUTED"


def test_an_adr_index_is_not_a_decision(plan):
    """`docs/adr/README.md` is the index OF the decisions. Every ADR directory has one,
    and it imported as a decision whose body was a table of contents."""
    decisions = [f.ident for f in plan.found if f.kind == "decision"]
    assert decisions == ["D-0001-postgres"], decisions


# -- the journal and the memory store --------------------------------------------------


def test_optmem_records_are_imported_with_their_own_dates(plan):
    """Months of "this box has 8 H200s" that no other source holds and that a fresh
    queue silently throws away."""
    mem = {f.ident: f for f in plan.found if f.kind == "memory"}
    assert set(mem) == {"M-0000", "M-0001", "M-0002"}, sorted(mem)
    assert mem["M-0000"].extra["at"] == "2026-07-31"
    assert "8x H200" in mem["M-0000"].body


def test_a_journal_entry_is_dated_by_when_it_HAPPENED(plan):
    """Otherwise every entry is dated the day the import ran, and the chronology — the
    one thing a journal is for — is gone."""
    dates = {f.title[:20]: f.extra.get("at") for f in plan.found if f.kind == "journal"}
    assert "2026-04-30" in dates.values(), dates  # from a trailing parenthetical
    assert "2026-04-22" in dates.values(), dates  # from a leading date


def test_the_dates_survive_into_recall(repo):
    """The fold used to drop every field but `at`, `text` and `item`, so the date, the
    source and the id went in and never came out."""
    _real(repo)
    run_cli(repo, "init")
    assert run_cli(repo, "import", "--apply")[0] == OK
    _code, out, _ = run_cli(repo, "recall", "H200 hardware idle", "--max-chars", "20000")
    assert "2026-07-31" in out, f"dated by the import, not by the memory:\n{out}"
    assert out.count("8x H200 GPUs") == 1, f"the one-line memory printed twice:\n{out}"


# -- what it refuses, and what it says ------------------------------------------------


def test_drift_between_a_shipped_heading_and_its_checkboxes_is_reported(plan):
    """32 of them on the real repository. One-sided risk: if the heading is right, the
    queue is about to hand out work that is already done."""
    assert any("say the work is finished" in n for n in plan.notes), plan.notes
    assert any("99" in n for n in plan.notes if "finished" in n), plan.notes


def test_a_phase_with_no_open_task_left_is_not_imported(repo):
    """790 phases and zero tasks reads as "this project has 790 phases of work"."""
    _real(repo)
    plan = IM.plan_import(repo, None, max_tasks=500)
    # `150.Z` is fully ticked and nothing needs it. `142.A` is also fully ticked but
    # `143.37` needs it, so it must survive — the two together are the whole rule.
    ids = _by_id(plan)
    assert "150.Z" not in ids, "a phase with nothing open and nothing needing it"
    assert "142.A" in ids, "…but not one that open work depends on"
    assert any("had no open task left" in n for n in plan.notes), plan.notes


def test_the_cap_withholds_the_phases_with_the_tasks(repo):
    """A queue of empty phases is not a smaller import, it is a misleading one."""
    _real(repo)
    plan = IM.plan_import(repo, None, max_tasks=1)
    assert [f.kind for f in plan.found if f.kind in ("task", "phase")] == []
    assert any("REFUSING" in n for n in plan.notes), plan.notes


def test_every_kind_the_scanners_produce_is_shown_to_a_human(repo):
    """The human surface listed five of the eight kinds, so a repository whose whole
    history is a journal and a memory store printed a header and nothing under it."""
    _real(repo)
    run_cli(repo, "init")
    _code, out, _ = run_cli(repo, "import")
    for kind in ("phase", "task", "lesson", "decision", "research", "journal", "memory"):
        assert f"{kind}(s):" in out, f"`import` never mentions {kind}s:\n{out}"


def test_every_kind_a_scanner_produces_is_also_WRITTEN(repo):
    """The preview and the writer are two enumerations of the same list.

    A scanner added without extending the writer produces items that appear in the
    proposal, are counted in the summary, and are then silently dropped by `--apply` —
    an import that reports what it did not do.
    """
    _real(repo)
    run_cli(repo, "init")
    _code, out, err = run_cli(repo, "--json", "import", "--apply")
    written = json.loads(out)["written"]
    found = {f["kind"] for f in json.loads(out)["found"]}
    assert found, err
    assert found <= set(IM.KINDS), sorted(found - set(IM.KINDS))
    assert found <= set(written), f"scanned but never written: {sorted(found - set(written))}"


def test_running_it_twice_over_the_real_shapes_changes_nothing(repo):
    """Ids are uniquified, and uniquifying against what is ALREADY imported would make
    the pair `L-x`, `L-x-2` come out `L-x-2`, `L-x-3` on the second run — an import
    advertised as idempotent duplicating its whole corpus every time it is re-run."""
    _real(repo)
    run_cli(repo, "init")
    assert run_cli(repo, "import", "--apply")[0] == OK
    before = run_cli(repo, "--json", "board")[1]
    code, out, _ = run_cli(repo, "import")
    assert code == NOTHING, f"a second look must find nothing new:\n{out}"
    assert run_cli(repo, "--json", "board")[1] == before


def test_the_knobs_are_configurable(repo):
    """`max_tasks` and the preview width were hardcoded; the flag now overrides the
    knob and 0 means "no flag given" rather than a default masquerading as a choice."""
    _real(repo)
    run_cli(repo, "init")
    (repo / ".orchard" / "config.toml").write_text("[importer]\nmax_tasks = 1\n")
    _code, out, err = run_cli(repo, "import")
    assert "REFUSING" in out + err, out + err
    _code, out, _ = run_cli(repo, "import", "--max-tasks", "500")
    assert "REFUSING" not in out, "an explicit flag must override the knob"


def test_the_offer_counts_every_source_not_three_of_them(repo):
    """The handshake decides whether to OFFER the import by counting source files.

    It counted the todo, lessons and ADR families only — so a project whose entire
    history is an engineering journal and a cross-session memory store was told
    nothing, which is exactly the project the offer exists for.
    """
    from orchard.surfaces.mcp import _instructions

    (repo / "docs" / "log").mkdir(parents=True)
    (repo / ".agent_memory").mkdir()
    (repo / "docs" / "log" / "2026-04.md").write_text(JOURNAL)
    (repo / ".agent_memory" / "LOG.txt").write_text(OPTMEM + "\n")
    run_cli(repo, "init")

    text = _instructions(repo)
    assert "orchard_import" in text, text[-800:]
    assert "queue is empty" in text.lower(), text[-800:]


# -- what the cross-family critic found in the import itself -------------------------


def test_a_dependency_on_an_ALREADY_IMPORTED_item_is_not_reported_as_missing(repo):
    """The incremental re-import path, which is the module's whole reason for existing.

    The unresolvable-needs check built its id set from `plan.found` alone — which by
    construction excludes everything already in the queue. So on the second run, a new
    task depending on a task imported by the FIRST run was reported as depending on an
    id "this scan did not find", with a consequence sentence that is simply false: the
    fold resolves dependencies against the folded queue, not against one scan's output,
    so that dependency is met and nothing needs correcting. A note whose stated
    consequence is wrong is worse than no note — it sends the operator to fix something
    that is not broken.
    """
    _real(repo)
    run_cli(repo, "init")
    assert run_cli(repo, "import", "--apply")[0] == OK

    todo = repo / "docs" / "todo.md"
    todo.write_text(todo.read_text() + "\n- [ ] **160.A.9** — a later idea\n  **Needs:** 160.A.1\n")

    plan = IM.plan_import(repo, _state(repo), max_tasks=500)
    assert any(f.ident == "160.A.9" for f in plan.found), [f.ident for f in plan.found]
    assert not any("160.A.1" in n for n in plan.notes if "did not find" in n), plan.notes


def test_an_annotation_cannot_attach_to_a_task_in_a_DIFFERENT_file(repo):
    """`found` is never reset between files, so `found[-1]` could be the previous
    file's last task.

    A `**Globs:**` line physically inside `b.md` can never be intended for a task in
    `a.md`, and the mis-attribution is invisible in the plan output because `source`
    still points at a's line — the operator sees a task with globs it never declared
    and no way to tell where they came from.
    """
    (repo / "docs" / "todo" / "open").mkdir(parents=True)
    (repo / "docs" / "todo" / "open" / "a.md").write_text(
        "## A\n\n- [ ] **A.1** — first file's last task\n"
    )
    (repo / "docs" / "todo" / "open" / "b.md").write_text(
        "**Globs:** src/b/**\n**Needs:** A.1\n\n## B\n\n- [ ] **B.1** — second file\n"
    )
    found, _empty = IM.scan_todos(repo)
    a1 = next(f for f in found if f.ident == "A.1")
    assert a1.globs == [], f"globs from b.md landed on a.md's task: {a1.globs}"
    assert a1.needs == [], f"a dependency from b.md landed on a.md's task: {a1.needs}"


def test_an_underscore_in_an_id_survives_the_needs_line(repo):
    """The two halves of one grammar disagreed.

    `_is_id` admits `TASK_1` — `\\w` includes the underscore — so it is emitted as an
    item's ident. The needs parser then stripped `_` as markdown decoration and
    recorded a dependency on `TASK1`, which no item has, so the dependent imports
    permanently blocked on a token that appears nowhere in the project.
    """
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "todo.md").write_text(
        "## P\n\n- [ ] **TASK_1** — first\n- [ ] **TASK_2** — second\n  **Needs:** `TASK_1`\n"
    )
    found, _empty = IM.scan_todos(repo)
    ids = {f.ident for f in found}
    assert "TASK_1" in ids, sorted(ids)
    t2 = next(f for f in found if f.ident == "TASK_2")
    assert t2.needs == ["TASK_1"], f"the underscore was stripped as decoration: {t2.needs}"


# -- the canary ------------------------------------------------------------------------


def _host_repo() -> Path | None:
    """The repository Orchard itself lives in, if it is a long-lived one."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docs" / "lessons.md").is_file() and (parent / ".git").exists():
            return parent
    return None


@pytest.mark.slow
def test_the_real_host_project_imports_cleanly():
    """Run the whole scan over the repository this lives in, when there is one.

    Skipped elsewhere, and deliberately NOT the proof of anything above — every
    assertion here is also a fixture test. What it adds is scale: a corpus nobody
    curated, where the next unparseable shape will appear before anyone writes it into
    a fixture.
    """
    repo = _host_repo()
    if repo is None:
        pytest.skip("not inside a project with docs/lessons.md")
    plan = IM.plan_import(repo, None, max_tasks=100_000)
    kinds = plan.summary()
    assert kinds.get("lesson", 0) > 0 and kinds.get("task", 0) > 0, kinds

    counts = collections.Counter(f.ident for f in plan.found)
    assert [i for i, n in counts.items() if n > 1] == [], "colliding ids lose data silently"

    ids = {f.ident for f in plan.found}
    unresolved = {d for f in plan.found for d in f.needs} - ids
    assert unresolved == set(), (
        f"{len(unresolved)} declared dependencies point at ids the scan did not "
        f"produce, so that work imports permanently blocked: {sorted(unresolved)[:10]}"
    )

    for f in plan.found:
        assert f.source, f
        assert f.title.strip(), f"an item with no title is unreadable in every view: {f}"


@pytest.mark.slow
def test_the_real_host_project_survives_a_round_trip(repo):
    """Scan the real corpus, write it, read it back through the ordinary views.

    Copied into a scratch repository first: an import writes events, and writing them
    into the project being read is not a test, it is an accident.
    """
    host = _host_repo()
    if host is None:
        pytest.skip("not inside a project with docs/lessons.md")
    for name in ("docs", ".agent_memory"):
        src = host / name
        if src.is_dir():
            subprocess.run(["cp", "-r", str(src), str(repo / name)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "corpus"], check=True, capture_output=True
    )

    run_cli(repo, "init")
    code, out, err = run_cli(repo, "import", "--apply", "--max-tasks", "100000")
    assert code == OK, out + err

    code, out, err = run_cli(repo, "doctor")
    assert code in (OK, FAIL), out + err
    assert "PROBLEM" not in out or "unknown item" not in out, (
        f"the queue it built blocks on ids it never created:\n{out}"
    )

    code, out, _ = run_cli(repo, "--json", "next")
    assert json.loads(out)["ready"], "an import that leaves nothing runnable is not one"

    code, out, _ = run_cli(repo, "import", "--max-tasks", "100000")
    assert code == NOTHING, f"re-running it must find nothing new:\n{out}"
