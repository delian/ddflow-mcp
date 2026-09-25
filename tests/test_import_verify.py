"""`ddflow import --verify` — was it imported, is it still true, did anyone finish it.

The import's weak spot was never the parsing. Run against a real 400-day corpus it
produced 1,170 tasks, and the `import-existing-project` prompt tells an agent to give
each one globs and declare its dependencies — and nothing checked whether that ever
happened. An imported queue nobody finished misrepresents the project exactly as an
empty one does, and is believed harder, because a tool produced it.

Three exit codes because there are three answers, and collapsing them loses the one that
matters: `2` nothing was ever imported · `1` imported, and here is what a human still has
to decide · `0` imported and consistent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

TODO = """# Roadmap

## Phase 7 — billing

- [ ] **P7.T1** — tax rules
  **Globs:** src/billing/tax.py
- [ ] **P7.T2** — invoice model
"""

LESSONS = "# Lessons\n\n## Never trust a float for money\nUse integer minor units.\n"
ADR = "# Use Postgres\n\n## Status\nAccepted\n\n## Decision\nConcurrent writers.\n"
RESEARCH = "# Research\n\n## SOAP versus Muon\n\nCONFIRMED by the probe.\n"
JOURNAL = "# 2026-04\n\n## Phase 3 closure (2026-04-30)\n\nIt shipped.\n"
OPTMEM = "#0 2026-07-31 Hardware: 8x H200 GPUs on this box.".ljust(317) + "\n"


def _legacy(repo: Path) -> Path:
    (repo / "docs" / "adr").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "log").mkdir(parents=True, exist_ok=True)
    (repo / ".agent_memory").mkdir(exist_ok=True)
    (repo / "docs" / "todo.md").write_text(TODO)
    (repo / "docs" / "lessons.md").write_text(LESSONS)
    (repo / "docs" / "RESEARCH.md").write_text(RESEARCH)
    (repo / "docs" / "adr" / "0001-postgres.md").write_text(ADR)
    (repo / "docs" / "log" / "2026-04.md").write_text(JOURNAL)
    (repo / ".agent_memory" / "LOG.txt").write_text(OPTMEM)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "history"], check=True, capture_output=True
    )
    return repo


def _imported(repo: Path) -> Path:
    _legacy(repo)
    run_cli(repo, "init")
    assert run_cli(repo, "import", "--apply")[0] == OK
    return repo


def _verify(repo: Path) -> tuple[int, dict]:
    code, out, err = run_cli(repo, "--json", "import", "--verify")
    assert out, f"no output at all (exit {code}):\n{err}"
    return code, json.loads(out)


# -- "nothing imported" is an answer, not a failure ------------------------------------


def test_a_project_with_no_import_says_so_and_exits_2(repo):
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "import", "--verify")
    assert code == NOTHING, out
    assert "Nothing in this queue was imported" in out, out


def test_a_hand_built_queue_is_not_mistaken_for_an_imported_one(repo):
    """Provenance is a FIELD. An item someone typed has none, and counting it as
    imported would make the whole report a confident fiction."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "p1/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "p1/a.py")
    code, data = _verify(repo)
    assert code == NOTHING, data
    assert data["total"] == 0, data["imported"]


# -- status ----------------------------------------------------------------------------


def test_it_counts_every_kind_it_imported(repo):
    _imported(repo)
    _code, data = _verify(repo)
    for kind in ("phase", "task", "lesson", "decision", "research", "journal", "memory"):
        assert data["imported"].get(kind), f"{kind} imported but not counted: {data['imported']}"
    assert data["total"] == sum(data["imported"].values())


def test_provenance_is_a_field_and_survives_the_fold(repo):
    """`Item.source`, not a regex over the body.

    The body says "Imported from docs/todo.md:41." and a human reads that in `show` —
    but "which items came from the import" is a question a machine has to answer, and
    answering it by matching a sentence means the day someone rewords the sentence the
    count silently becomes zero and the verification passes.
    """
    _imported(repo)
    _code, out, _ = run_cli(repo, "--json", "show", "P7.T1")
    item = json.loads(out)
    assert item["source"].startswith("docs/todo.md:"), item.get("source")
    assert "Imported from" in item["body"], "the human-readable half must stay too"


def test_completion_evidence_is_readable_after_the_fold(repo):
    """The importer has always written `{"imported": True, "evidence": "ticked in
    docs/todo.md:41"}` on a completed item, and the fold threw both away — so the one
    record of why an item was closed without running a single gate existed nowhere a
    reader could reach."""
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "todo.md").write_text(
        "## P\n\n- [x] **P.1** — done long ago\n- [ ] **P.2** — open\n  **Needs:** P.1\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "h"], check=True, capture_output=True)
    run_cli(repo, "init")
    assert run_cli(repo, "import", "--apply")[0] == OK

    _code, out, _ = run_cli(repo, "--json", "show", "P.1")
    assert "ticked in docs/todo.md" in json.loads(out)["completion_evidence"]


# -- did anyone finish it --------------------------------------------------------------


def test_an_imported_task_with_no_globs_is_reported(repo):
    """The commonest leftover and the one with teeth: the conflict detector cannot
    protect a task that has not said what it writes, so two agents can be handed the
    same file and neither is refused."""
    _imported(repo)
    code, data = _verify(repo)
    assert code == FAIL, data
    assert data["tasks_without_globs"] == ["P7.T2"], data["tasks_without_globs"]
    assert any("conflict detector" in f for f in data["findings"]), data["findings"]


def test_it_stops_reporting_a_task_once_its_globs_are_declared(repo):
    """A finding that cannot be cleared teaches the reader to skip the whole report."""
    _imported(repo)
    assert run_cli(repo, "update", "P7.T2", "--globs", "src/billing/invoice.py")[0] == OK
    code, data = _verify(repo)
    assert data["tasks_without_globs"] == [], data["tasks_without_globs"]
    assert code == OK, data["findings"]


def test_a_phase_claiming_SHIPPED_over_an_open_task_is_reported(repo):
    """27 of them in the repository this was measured against. One-sided risk: if the
    heading is right, the queue is about to hand out work that is already done."""
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "todo.md").write_text(
        "## Phase 9 — the thing — **SHIPPED 2026-06-01**\n\n"
        "- [ ] **P9.T1** — the loose end nobody ticked\n  **Globs:** a.py\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "h"], check=True, capture_output=True)
    run_cli(repo, "init")
    run_cli(repo, "import", "--apply")

    code, data = _verify(repo)
    assert code == FAIL, data
    assert data["shipped_with_open_tasks"], data
    assert any("finished while" in f for f in data["findings"]), data["findings"]


# -- is it still true ------------------------------------------------------------------


def test_it_reports_what_the_source_gained_since_the_import(repo):
    _imported(repo)
    todo = repo / "docs" / "todo.md"
    todo.write_text(todo.read_text() + "\n- [ ] **P7.T3** — added after the import\n")
    _code, data = _verify(repo)
    assert any(f["id"] == "P7.T3" for f in data["new_since_import"]), data["new_since_import"]
    assert any("moved on" in f for f in data["findings"]), data["findings"]


def test_an_untouched_source_reports_no_drift(repo):
    """The other half of the same claim: a check that always fires is not a check."""
    _imported(repo)
    run_cli(repo, "update", "P7.T2", "--globs", "src/billing/invoice.py")
    _code, data = _verify(repo)
    assert data["new_since_import"] == [], data["new_since_import"]


def test_a_vanished_source_file_is_named(repo):
    """The provenance of those items cannot be checked any more, and silence about that
    reads as "checked and fine"."""
    _imported(repo)
    (repo / "docs" / "todo.md").unlink()
    _code, data = _verify(repo)
    assert data["vanished_sources"], data
    assert data["vanished_sources"][0]["source"] == "docs/todo.md"
    assert any("no longer exists" in f for f in data["findings"]), data["findings"]


# -- it does not repeat what doctor already says ---------------------------------------


def test_it_leaves_dependency_resolution_to_doctor(repo):
    """Two commands reporting one defect in different words is how an operator learns
    to read neither. `doctor` has always reported unknown dependencies."""
    _imported(repo)
    todo = repo / "docs" / "todo.md"
    todo.write_text(todo.read_text() + "\n- [ ] **P7.T9** — x\n  **Needs:** NOPE.1\n")
    run_cli(repo, "import", "--apply")

    _code, data = _verify(repo)
    assert not any("NOPE.1" in f for f in data["findings"]), data["findings"]
    assert any("doctor" in n for n in data["notes"]), data["notes"]

    code, out, _ = run_cli(repo, "doctor")
    assert code == FAIL and "NOPE.1" in out, out


# -- the two surfaces agree ------------------------------------------------------------


def test_the_human_and_json_surfaces_give_the_same_verdict(repo):
    _imported(repo)
    code, data = _verify(repo)
    human_code, out, _ = run_cli(repo, "import", "--verify")
    assert human_code == code
    assert data["verified"] is (code == OK)
    assert ("Left to decide or fix" in out) is bool(data["findings"]), out


def test_verify_and_apply_together_are_refused_rather_than_resolved(repo):
    """One READS and one WRITES. Picking either silently is how an operator who asked
    to import ends up having only looked."""
    _imported(repo)
    code, _out, err = run_cli(repo, "import", "--verify", "--apply")
    assert code == FAIL
    assert "different things" in err, err


def test_it_is_reachable_over_mcp(repo):
    from ddflow.surfaces.mcp import TOOLS

    assert "ddflow_import_verify" in TOOLS, sorted(TOOLS)
    assert TOOLS["ddflow_import_verify"]["argv"]({}) == ["--json", "import", "--verify"]


# -- the handshake follows through -----------------------------------------------------


def test_the_handshake_says_the_import_was_never_finished(repo):
    """The offer to import stops the moment the queue has anything in it. Without this
    block, an import that landed 1,170 tasks and stopped there is never mentioned
    again — and the agent has no reason to think anything is missing."""
    from ddflow.surfaces.mcp import _instructions

    _imported(repo)
    text = _instructions(repo)
    assert "ddflow_import_verify" in text, text[-1200:]
    assert "declare no globs" in text, text[-1200:]


def test_the_handshake_stays_quiet_once_the_import_is_finished(repo):
    """A standing banner is one readers learn to skip, and then they skip the one that
    mattered."""
    from ddflow.surfaces.mcp import _instructions

    _imported(repo)
    run_cli(repo, "update", "P7.T2", "--globs", "src/billing/invoice.py")
    assert "never finished" not in _instructions(repo)


def test_the_handshake_does_not_rescan_the_sources(repo, monkeypatch):
    """The queue-only half costs nothing because the state is already folded; a full
    source scan is ~0.65 s on a real corpus and would be paid at EVERY session start.

    Asserted by making the scan EXPLODE. `_instruction_vars` is failure-tolerant by
    design, so a handshake that scanned would swallow the error and report zero — which
    is why the assertion is on the counts being right, not on the absence of a crash.
    Deleting the source files instead would prove nothing: the three counts it reports
    come from the queue either way.
    """
    from ddflow.services import importer as IM
    from ddflow.surfaces.mcp import _instruction_vars

    _imported(repo)

    def boom(*_a, **_k):
        raise AssertionError("the handshake re-scanned the source files")

    monkeypatch.setattr(IM, "plan_import", boom)
    v = _instruction_vars(repo)
    assert v["imported_total"] > 0, "the cheap half must still work without a scan"
    assert v["imported_no_globs"] == 1, v


# -- what the adversarial review found -------------------------------------------------


def test_every_item_whose_source_vanished_is_named_not_just_the_first(repo):
    """`vanished` was built per FILE and reported per ITEM.

    Four items sharing `docs/todo.md` produced ONE entry, because only `ids[0]` was
    kept — while the docstring, the finding text and the JSON key all promised items.
    An agent auditing provenance fixes the one it was told about and believes it has
    finished.
    """
    _imported(repo)
    (repo / "docs" / "todo.md").unlink()
    _code, data = _verify(repo)
    named = {v["item"] for v in data["vanished_sources"]}
    assert {"P7.T1", "P7.T2"} <= named, f"only {named} named; the rest are silent"
    assert any(str(len(named)) in f for f in data["findings"]), data["findings"]


def test_a_project_that_never_imported_is_not_reported_as_VERIFIED(repo):
    """The vacuous-truth class, in the field whose whole job is to say "checked".

    `verified: true` with `imported: {}` reads as "the import is in good order" to
    anything looking at the JSON — and over MCP exit 2 is not an error, so the only
    contradicting signal is in `_meta`, which most clients drop.
    """
    run_cli(repo, "init")
    code, data = _verify(repo)
    assert code == NOTHING
    assert data["verified"] is False, "nothing was verified, so nothing is verified"
    assert data["imported_anything"] is False, data


def test_a_glob_wrapped_in_backticks_is_not_counted_as_a_declared_glob(repo):
    """`**Globs:** `src/a.py`` folded to `['`src/a.py`']` — backticks included.

    `verify` tested `not it.globs`, which is False, so the task was reported as
    protected. It is not: the conflict detector compares glob strings, and
    `fnmatch('`src/a.py`', 'src/a.py')` is False either way round, as is the prefix
    fallback. The exact scenario this check exists to prevent — two agents handed the
    same file, neither refused — survived a clean verify.

    The same asymmetry the `**Needs:**` parser already fixed: two halves of one grammar
    have to agree about what is decoration.
    """
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "todo.md").write_text(
        "## P\n\n- [ ] **P.1** — polish\n  **Globs:** `src/polish.py`, docs/**\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "h"], check=True, capture_output=True)
    run_cli(repo, "init")
    run_cli(repo, "import", "--apply")

    _code, out, _ = run_cli(repo, "--json", "show", "P.1")
    globs = json.loads(out)["globs"]
    assert globs == ["src/polish.py", "docs/**"], f"markdown kept as part of a path: {globs}"


def test_a_branch_import_is_not_reported_as_a_task_without_globs(repo):
    """Branches import as tasks with no globs BY CONSTRUCTION, so every repo with an
    unmerged branch got a permanent "the import was never finished" banner — and the
    finding said "N imported task(s)" while the count above it said "1 branch(s)"."""
    _legacy(repo)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "-b", "feature/extra"],
        check=True,
        capture_output=True,
    )
    (repo / "wip.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "wip"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "main"], check=True, capture_output=True
    )
    run_cli(repo, "init")
    run_cli(repo, "import", "--apply")

    _code, data = _verify(repo)
    assert data["imported"].get("branch") == 1, data["imported"]
    assert not any(i.startswith("B-") for i in data["tasks_without_globs"]), (
        f"a branch is counted under 'branch' and reported under 'task': "
        f"{data['tasks_without_globs']}"
    )
    assert data["branches_without_globs"], "…but it still has to be reported somewhere"


def test_prose_only_provenance_is_a_note_not_a_permanent_finding(repo):
    """An item imported before provenance was a field can never be fixed — the text
    says so itself. A finding nobody can clear is a latch that keeps `verified` false
    forever, and a report that can never go green is one people stop reading."""
    _imported(repo)
    run_cli(repo, "update", "P7.T2", "--globs", "src/billing/invoice.py")
    run_cli(repo, "phase", "add", "LEGACY", "--globs", "x/**")
    # Simulate the old shape: prose body, no `source` field.
    shard = next((repo / ".ddflow" / "events").glob("*.jsonl"))
    shard.write_text(
        shard.read_text()
        + json.dumps(
            {
                "kind": "task.added",
                "subject": "OLD.1",
                "data": {"title": "old", "body": "Imported from docs/legacy.md:3."},
                "agent": "a",
                "lamport": 99999,
                "ts": "2026-01-01T00:00:00Z",
                "id": "x" * 16,
            }
        )
        + "\n"
    )
    code, data = _verify(repo)
    assert data["unstructured_provenance"] == ["OLD.1"], data["unstructured_provenance"]
    assert not any("prose" in f for f in data["findings"]), (
        f"an unfixable fact is a note, not a finding: {data['findings']}"
    )
    assert any("prose" in n for n in data["notes"]), data["notes"]
    assert code == OK, f"nothing is actionable, so this must be green: {data['findings']}"


def test_verify_refuses_the_flags_it_would_otherwise_ignore(repo):
    """`--include-done` and `--max-tasks` shape an IMPORT, not a report on one. Reading
    past them silently is the silent-knob-drop shape, right next to a flag that is
    loudly refused."""
    _imported(repo)
    for flag in (["--include-done"], ["--max-tasks", "5"]):
        code, _out, err = run_cli(repo, "import", "--verify", *flag)
        assert code == FAIL, f"{flag} was silently ignored"
        assert "--verify" in err, err


def test_a_hand_written_note_in_the_import_session_is_not_counted_as_imported(repo):
    """`session note s-imported-journal --text "..."` is an ordinary command that takes
    any session id, so the import's own sinks are not private to it.

    Counting `len(sess.notes)` therefore reported more imported records than were
    imported — the same missing provenance filter the lesson/decision/research loop
    three lines above has, and the whole reason provenance became a field.
    """
    _imported(repo)
    _code, before = _verify(repo)
    assert run_cli(repo, "session", "note", "s-imported-journal", "--text", "by hand")[0] == OK
    _code, after = _verify(repo)
    assert after["imported"]["journal"] == before["imported"]["journal"], (
        f"a hand-written note inflated the count: "
        f"{before['imported']['journal']} -> {after['imported']['journal']}"
    )


def test_a_vanished_lesson_or_research_source_is_named_too(repo):
    """Lessons carry `seen_in` and research carries `sources`, both structured, so the
    still-true check has no excuse to cover only items."""
    _imported(repo)
    (repo / "docs" / "lessons.md").unlink()
    (repo / "docs" / "RESEARCH.md").unlink()
    _code, data = _verify(repo)
    gone = {v["source"] for v in data["vanished_sources"]}
    assert "docs/lessons.md" in gone, gone
    assert "docs/RESEARCH.md" in gone, gone
