"""Importing a real project's plan and memory without flattening what it MEANS.

Measured against the two projects this was built to take over, the first import:

* offered every deferred, refuted and declined finding as ready work — 1,179 open tasks
  on a repository whose own picker offers ~120, because an unticked box was read as
  "work" whatever the words beside it said;
* collapsed 176 numbered lessons (`### L100.` under `## <date>` groups) into 24 date-groups;
* never read a 287-entry journal kept in `docs/LOG.md` (the patterns said `docs/log.md`);
* had nowhere to put a lesson's one-paragraph summary, which both projects keep.

Each test below plants the shape that was lost and asserts it survives.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import ABANDONED, BLOCKED, OPEN, fold
from ddflow.infra.log import EventLog
from ddflow.services import importer as IM

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _tasks(repo: Path, **kw) -> dict[str, IM.Found]:
    plan = IM.plan_import(repo, None, max_tasks=10**6, **kw)
    return {f.ident: f for f in plan.found if f.kind == "task"}


def _state(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False)


# -- dispositions: an open box is not always work ---------------------------------------

DISPOSED = """# Plan

## Session A — live work
**STATUS**: IN PROGRESS — 1 of 9 shipped

- [ ] **A.1** — do the thing
- [ ] **A.2 — make the sampler handle SKIPPED batches**
- [ ] **A.3** — a finding (DEFERRED by the operator)
- [ ] **A.4 (THEORETICAL — no code change)** — maybe a bug
- [ ] **A.5** — REFUTED: raised and then refuted, recorded so it is not re-litigated
- [ ] **A.6 (roborev 72 — pre-existing, HIGH)** — fully specified work
- [ ] **A.7** — (PRE-EXISTING) left alone on purpose
- [ ] ~~**A.8** — struck through~~
- [ ] **A.9** — the UNBLOCKED path needs a test

### Deferred

- [ ] **A.10** — nothing on this line says so; only its heading does

## Session B — long done
**STATUS**: SHIPPED — see commit `abc1234`

- [ ] **B.1** — a note left unticked in a shipped section

## Session C — parked
**STATUS**: DEFERRED until the vendor ships

- [ ] **C.1** — waits for the vendor
"""


def test_a_marker_that_ANNOTATES_an_item_disposes_of_it(repo):
    _write(repo, "docs/todo.md", DISPOSED)
    t = _tasks(repo, include_done=True)
    disp = {i: t[i].extra.get("disposition") for i in t}
    assert disp["A.3"] == "hold", "an annotation saying DEFERRED is a disposition"
    assert disp["A.4"] == "hold", "a parenthesised aside in the title is a disposition"
    assert disp["A.5"] == "closed", "REFUTED closes"
    assert disp["A.7"] == "hold", "PRE-EXISTING after the title is a disposition"
    assert disp["A.8"] == "closed", "struck through is closed"


def test_a_marker_in_bare_title_prose_does_NOT(repo):
    """`make the sampler handle SKIPPED batches` is the item that fixes skipping; a naive
    scan hides exactly the work that needs doing.

    "Title" is the first bold run, which is the source picker's rule (`phase.py
    ::_annotation_markers`) -- this classifier agrees with it on all 1,170 open boxes of
    the project it was measured on. Where only the id is bold, the words after it are
    annotation, as they are there."""
    _write(repo, "docs/todo.md", DISPOSED)
    t = _tasks(repo, include_done=True)
    assert t["A.1"].extra.get("disposition") == ""
    assert t["A.2"].extra.get("disposition") == "", "title prose mentioning SKIPPED is work"
    assert t["A.6"].extra.get("disposition") == "", (
        "PRE-EXISTING inside the title aside records WHEN a bug originated, not a decision"
    )
    assert t["A.9"].extra.get("disposition") == "", "UNBLOCKED is not BLOCKED"


def test_headings_and_STATUS_lines_dispose_of_everything_under_them(repo):
    _write(repo, "docs/todo.md", DISPOSED)
    t = _tasks(repo, include_done=True)
    assert t["A.10"].extra.get("disposition") == "hold", "a `### Deferred` sub-heading holds"
    assert t["B.1"].extra.get("disposition") == "closed", "STATUS: SHIPPED makes it history"
    assert t["C.1"].extra.get("disposition") == "hold", "STATUS: DEFERRED holds"


def test_a_STATUS_verdict_is_its_leading_token_not_the_whole_line(repo):
    """`IN PROGRESS — 373 of 534 (B.5 CLOSED)` is a live section. Reading the whole line
    found CLOSED and discarded the section holding the next piece of work."""
    _write(
        repo,
        "docs/todo.md",
        "## S\n**STATUS**: IN PROGRESS — 3 of 9 (**B.5 … CLOSED 2026-08-17**)\n\n"
        "- [ ] **S.1** — live\n",
    )
    assert _tasks(repo)["S.1"].extra.get("disposition") == ""


def test_the_import_holds_deferred_work_and_leaves_declined_work_out(repo):
    _write(repo, "docs/todo.md", DISPOSED)
    code, out, err = run_cli(repo, "--json", "import", "--apply")
    assert code == OK, err
    st = _state(repo)
    assert "A.5" not in st.items and "B.1" not in st.items, "closed work imported as open"
    held = st.items["A.3"]
    assert held.state == BLOCKED
    assert "DEFERRED" in held.blocked_reason and "docs/todo.md" in held.blocked_reason
    notes = " ".join(json.loads(out)["notes"])
    assert "NOT imported because the source disposes" in notes
    assert "import as BLOCKED" in notes


def test_held_work_is_never_offered_and_unblock_releases_it(repo):
    _write(repo, "docs/todo.md", DISPOSED)
    run_cli(repo, "import", "--apply")
    code, out, _ = run_cli(repo, "--json", "next")
    ready = {r["id"] for r in json.loads(out).get("ready", [])}
    assert "A.3" not in ready and "C.1" not in ready
    assert "A.1" in ready

    code, out, err = run_cli(repo, "--json", "unblock", "A.3", "--note", "operator said go")
    assert code == OK, err
    assert json.loads(out)["released"] == ["A.3"]
    assert _state(repo).items["A.3"].state == OPEN
    assert _state(repo).items["A.3"].blocked_reason == ""

    code, _out, _err = run_cli(repo, "unblock", "A.3")
    assert code == NOTHING, "releasing something that is not held is not a success"


def test_include_done_brings_closed_work_in_as_ABANDONED(repo):
    _write(repo, "docs/todo.md", DISPOSED)
    run_cli(repo, "import", "--apply", "--include-done")
    st = _state(repo)
    assert st.items["A.5"].state == ABANDONED
    assert "REFUTED" in st.items["A.5"].blocked_reason


def test_a_closed_item_that_open_work_NEEDS_is_imported_so_the_need_resolves(repo):
    _write(
        repo,
        "docs/todo.md",
        "## S\n\n- [ ] **S.1** — DECLINED: was the old approach\n"
        "- [ ] **S.2** — the new approach\n  **Needs:** S.1\n",
    )
    t = _tasks(repo)
    assert "S.1" in t, "a need on a closed item would otherwise import permanently blocked"


# -- archives: a plan file that is history until a section is named ------------------------


def test_an_archive_files_open_work_is_held_and_releasable_by_phase(repo):
    _write(
        repo,
        "docs/todo.md",
        "## Legacy\n\n### 159.A — old group\n\n- [ ] **159.A.1** — one\n- [ ] **159.A.2** — two\n",
    )
    _write(repo, "docs/todo/open/NEW.md", "## New\n\n- [ ] **NEW.1** — live\n")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[importer]\narchive_globs = ["docs/todo.md"]\n')
    code, _out, err = run_cli(repo, "import", "--apply")
    assert code == OK, err
    st = _state(repo)
    assert st.items["NEW.1"].state == OPEN
    assert st.items["159.A.1"].state == BLOCKED
    assert "archive" in st.items["159.A.1"].blocked_reason

    code, out, err = run_cli(repo, "--json", "unblock", "159.A")
    assert code == OK, err
    assert json.loads(out)["released"] == ["159.A.1", "159.A.2"]
    st = _state(repo)
    assert st.items["159.A.1"].state == OPEN and st.items["159.A.2"].state == OPEN


# -- lessons at the level they actually live at ----------------------------------------------

NESTED_LESSONS = """# Lessons

Newest first.

## 2026-06-28 — Phase 24 multiprocess fan-out

Some prose about the day that is not a lesson.

### L100 (reinforces L99). Threads and async share one GIL — fan out across PROCESSES
**What:** the fleet idled.
**Rule/Process:** use processes.

### L99. A GPU fleet needs a client that can keep up
**What:** clients were the bottleneck.

## 2026-06-27 — Dedup session

Group prose for the second day, which belongs to no lesson either.

### L98. Grep the OPERATION, not just the def
**What:** a copy diverged. See [L52].
"""


def test_numbered_lessons_under_date_groups_import_one_per_lesson_with_their_ids(repo):
    _write(repo, "docs/lessons.md", NESTED_LESSONS)
    found, _empty = IM.scan_lessons(repo)
    by_id = {f.ident: f for f in found}
    assert set(by_id) == {"L100", "L99", "L98"}, sorted(by_id)
    assert by_id["L100"].title.startswith("Threads and async share one GIL")
    assert "use processes" in by_id["L100"].body
    # L99 is the last entry of the first group: without the group boundary, the next
    # group's heading and prose are read as the tail of L99's body.
    assert "Dedup session" not in by_id["L99"].body, "a group heading leaked into an entry"
    assert "second day" not in by_id["L99"].body, "a group's prose leaked into an entry"
    assert "[L52]" in by_id["L98"].body, "cross-references are kept"


def test_a_flat_corpus_still_splits_at_its_top_level(repo):
    _write(repo, "docs/lessons.md", "# Lessons\n\n## One rule\nbody\n\n## Two rule\nbody\n")
    found, _ = IM.scan_lessons(repo)
    assert [f.title for f in found] == ["One rule", "Two rule"]


def test_a_Compressed_paragraph_becomes_the_summary_and_Seen_in_the_provenance(repo):
    _write(
        repo,
        "docs/lessons.md",
        "# Lessons\n\n## Give the order ONE home\n\n**Compressed:** two functions\n"
        "resolved rank differently.\n\nLonger discussion.\n\n**Seen in:** `EVALRANK.1`, roborev 812\n",
    )
    run_cli(repo, "import", "--apply")
    ls = next(iter(_state(repo).lessons.values()))
    assert ls.summary == "two functions resolved rank differently."
    assert any("EVALRANK.1" in s for s in ls.seen_in)


# -- the lessons summary ----------------------------------------------------------------------

SUMMARY = """# Lessons — summary

## Process
- **Run the rubber-duck** before declaring done. [L99]
- **Mirror the downstream consumer's config.** Model ids and paths. [L98/L100]

## Correctness
- **An rng-ordered collection is part of the output contract.**
  Adding to it changes every record.
"""


def test_a_bullet_citing_one_lesson_becomes_its_summary_and_the_rest_become_lessons(repo):
    _write(repo, "docs/lessons.md", NESTED_LESSONS)
    _write(repo, "docs/lessons-summary.md", SUMMARY)
    code, _out, err = run_cli(repo, "--json", "import", "--apply")
    assert code == OK, err
    st = _state(repo)
    assert st.lessons["L99"].summary.startswith("**Run the rubber-duck**")
    consolidated = [ls for ls in st.lessons.values() if "summary" in ls.tags]
    assert len(consolidated) == 2, [ls.id for ls in consolidated]
    multi = next(ls for ls in consolidated if "downstream" in ls.title)
    assert {"L98", "L100"} <= set(multi.seen_in)
    rng = next(ls for ls in consolidated if "rng-ordered" in ls.title)
    assert "every record" in rng.summary, "a bullet's continuation line was lost"
    assert "correctness" in rng.tags


def test_a_GENERATED_summary_is_not_imported_twice(repo):
    _write(repo, "docs/lessons.md", "# Lessons\n\n## A rule\n**Compressed:** short.\n")
    _write(
        repo,
        "docs/lessons-summary.md",
        "# Summary\n\n<!-- GENERATED by a script — DO NOT EDIT -->\n\n## A rule\nshort.\n- x [L1]\n",
    )
    plan = IM.plan_import(repo, None)
    assert [f.kind for f in plan.found] == ["lesson"]
    assert any("GENERATED" in n for n in plan.notes)


def test_the_summary_view_uses_the_summary_and_says_when_it_fell_back(repo):
    run_cli(repo, "init")
    run_cli(
        repo,
        "lesson",
        "add",
        "--id",
        "L1",
        "--title",
        "Curated",
        "--rule",
        "long rule",
        "--summary",
        "one paragraph",
        "--tags",
        "process",
    )
    run_cli(
        repo,
        "lesson",
        "add",
        "--id",
        "L2",
        "--title",
        "Uncurated",
        "--rule",
        "First paragraph.\n\nSecond paragraph.",
    )
    code, out, err = run_cli(repo, "render", "--show", "lessons-summary")
    assert code == OK, err
    assert "one paragraph" in out and "long rule" not in out
    assert "First paragraph." in out and "Second paragraph." not in out
    assert "_(first paragraph)_" in out.split("Uncurated", 1)[1].splitlines()[0]
    assert "## process" in out


# -- where the journal lives -----------------------------------------------------------------


def test_an_upper_case_LOG_md_journal_is_read_and_a_generated_index_is_not(repo):
    _write(
        repo,
        "docs/LOG.md",
        "# Log\n\n## 2026-07-06 — did a thing\nwhat\n\n## 2026-07-05 — before\nx\n",
    )
    found, _ = IM.scan_journal(repo)
    assert [f.title for f in found] == ["2026-07-06 — did a thing", "2026-07-05 — before"]

    _write(
        repo, "docs/LOG.md", "# Change Log\n\n## Index\n\n### [2026-09](log/2026-09.md)\n- [x](y)\n"
    )
    found, empty = IM.scan_journal(repo)
    assert found == [] and "docs/LOG.md" in empty


def test_configured_globs_REPLACE_the_defaults(repo):
    _write(repo, "docs/lessons.md", "# L\n\n## default rule\nx\n")
    _write(repo, "notes/rules.md", "# L\n\n## configured rule\nx\n")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[importer]\nlesson_globs = ["notes/rules.md"]\n')
    code, out, err = run_cli(repo, "--json", "import")
    assert code == OK, err
    titles = {f["title"] for f in json.loads(out)["found"] if f["kind"] == "lesson"}
    assert titles == {"configured rule"}
