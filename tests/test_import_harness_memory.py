"""Onboarding imports the harness's own project memory: found, deduplicated, approved.

The harness's memory is the operator's text; the facts are not derivable from the code.
So the import has to find the right store (the slug encodes the checkout path), read it
against its own index, and never double the queue on a re-run.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.core.model import Memory, State, fold
from ddflow.infra.log import EventLog
from ddflow.services import importer_harness as H

FACT = """---
name: use-xdist
description: Run tests with -n 48; serial takes 24 minutes and the gate times out
metadata:
  node_type: memory
  type: feedback
  modified: 2026-09-29T11:00:00.000Z
---

Delian (2026-09-28): run tests in parallel.

**How:** `-n 48`.
"""

INDEX = "- [Run tests in parallel](use-xdist.md) — -n 48\n"


def _memory_dir(tmp_path: Path, repo: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "claude-projects"
    memory = root / H.project_slug(repo) / "memory"
    memory.mkdir(parents=True)
    for name, text in files.items():
        (memory / name).write_text(text)
    return root


def _state_with(text: str) -> State:
    state = State()
    state.memories["M-existing"] = Memory(id="M-existing", text=text)
    return state


def _cfg(repo: Path) -> Config:
    """The suite turns the add-time duplicate check off by default (conftest); the
    import's dedupe IS this file's subject, so turn the ask back on."""
    cfg = Config.load(repo)
    cfg.dedupe.on_match = "ask"
    return cfg


def test_the_slug_encodes_the_path_the_way_the_harness_does():
    assert H.project_slug(Path("/home/delian/src/run_nemo_run")) == "-home-delian-src-run-nemo-run"
    assert (
        H.project_slug(Path("/x/.ddflow-worktrees/B-onboard-memory"))
        == "-x--ddflow-worktrees-B-onboard-memory"
    )


def test_the_memory_directory_is_found_by_slug(repo, tmp_path):
    root = _memory_dir(tmp_path, repo, {"MEMORY.md": ""})
    expected = root / H.project_slug(repo) / "memory"
    assert H.harness_memory_dir(repo, projects_root=root) == expected
    assert H.harness_memory_dir(repo, projects_root=tmp_path / "nope") is None


def test_scan_reads_the_fact_and_its_origin(repo, tmp_path):
    root = _memory_dir(tmp_path, repo, {"MEMORY.md": INDEX, "use-xdist.md": FACT})
    scan = H.scan(repo, projects_root=root)
    assert scan.problems == []
    assert [f.ident for f in scan.found] == ["M-harness-use-xdist"]
    fact = scan.found[0]
    assert fact.title == "use-xdist"
    assert fact.body == "Run tests with -n 48; serial takes 24 minutes and the gate times out"
    assert fact.source == "claude-memory/use-xdist.md"
    assert fact.extra["origin_at"] == "2026-09-29T11:00:00.000Z"


def test_the_index_is_read_for_checks_not_imported(repo, tmp_path):
    root = _memory_dir(tmp_path, repo, {"MEMORY.md": INDEX, "use-xdist.md": FACT})
    scan = H.scan(repo, projects_root=root)
    assert all(f.source != "claude-memory/MEMORY.md" for f in scan.found)


def test_a_fact_missing_from_the_index_and_a_link_with_no_file_are_reported(repo, tmp_path):
    """Read the store against its own index (`L-import-read-against-handoff`)."""
    root = _memory_dir(
        tmp_path,
        repo,
        {
            "MEMORY.md": "- [Gone](gone.md)\n- [Known](known.md)\n",
            "known.md": FACT,
            "stray.md": FACT.replace("use-xdist", "stray"),
        },
    )
    scan = H.scan(repo, projects_root=root)
    assert any("links gone.md" in p for p in scan.problems)
    assert any("stray.md is not listed" in p for p in scan.problems)


def test_a_fact_without_a_description_uses_the_first_paragraph(repo, tmp_path):
    body = "---\nname: plain\n---\n\nFirst line of the fact.\n\nMore detail.\n"
    root = _memory_dir(tmp_path, repo, {"plain.md": body})
    scan = H.scan(repo, projects_root=root)
    assert [f.body for f in scan.found] == ["First line of the fact."]


def test_a_file_that_cannot_be_read_is_a_problem_not_a_crash(repo, tmp_path):
    broken = "---\nname: x\nmetadata:\n\tbroken: 1\n---\nbody\n"
    root = _memory_dir(tmp_path, repo, {"broken.md": broken})
    scan = H.scan(repo, projects_root=root)
    assert scan.found == []
    assert any("broken.md" in p and "frontmatter" in p for p in scan.problems)


def test_a_fact_with_nothing_in_it_is_reported(repo, tmp_path):
    root = _memory_dir(tmp_path, repo, {"empty.md": "---\nname: empty\n---\n"})
    scan = H.scan(repo, projects_root=root)
    assert scan.found == []
    assert any("no description and no body" in p for p in scan.problems)


def test_no_directory_is_not_an_error_but_says_so(repo, tmp_path):
    scan = H.scan(repo, projects_root=tmp_path / "nothing")
    assert scan.directory is None and scan.found == []
    assert "nothing to import" in H.render(scan, [], [])


def test_an_identical_memory_is_a_duplicate_not_a_second_fact(repo, tmp_path):
    root = _memory_dir(tmp_path, repo, {"use-xdist.md": FACT})
    scan = H.scan(repo, projects_root=root)
    kept, duplicates = H.dedupe(scan.found, _state_with(scan.found[0].body), _cfg(repo))
    assert kept == []
    assert duplicates[0].of == "M-existing" and duplicates[0].identical


def test_a_near_copy_over_the_threshold_is_asked_not_kept_silently(repo, tmp_path):
    """`min_words` counts the NEW record's content words, so the fact has to be long
    enough for the ask rule to apply at all (12 here); the short case below is the rule
    working, not a miss."""
    long_fact = (
        "---\nname: xdist-long\ndescription: Run the suite with -n 48 because serial "
        "runs take 24 minutes and the unit_tests gate times out every time\n---\n"
    )
    root = _memory_dir(tmp_path, repo, {"xdist-long.md": long_fact})
    scan = H.scan(repo, projects_root=root)
    near = (
        "Run the suite with -n 48 because serial runs take 24 minutes and the unit_tests "
        "gate times out every single time"
    )
    kept, duplicates = H.dedupe(scan.found, _state_with(near), _cfg(repo))
    assert kept == []
    assert duplicates and not duplicates[0].identical


def test_with_on_match_off_nothing_is_dropped(repo, tmp_path):
    """The knob means what it says: a project that turned the duplicate check off keeps
    every fact (critic on 2c18772)."""
    long_fact = (
        "---\nname: xdist-long\ndescription: Run the suite with -n 48 because serial "
        "runs take 24 minutes and the unit_tests gate times out every time\n---\n"
    )
    root = _memory_dir(tmp_path, repo, {"xdist-long.md": long_fact})
    scan = H.scan(repo, projects_root=root)
    near = (
        "Run the suite with -n 48 because serial runs take 24 minutes and the unit_tests "
        "gate times out every single time"
    )
    cfg = Config.load(repo)  # the suite's env leaves on_match off
    assert cfg.dedupe.on_match == "off"
    kept, duplicates = H.dedupe(scan.found, _state_with(near), cfg)
    assert [f.ident for f in kept] == ["M-harness-xdist-long"] and duplicates == []


def test_a_short_near_copy_is_kept_because_the_rule_needs_min_words(repo, tmp_path):
    """`[dedupe].min_words` exists so a two-word fact does not match everything; a
    short memory that scores 1.0 against an existing one is the rule working."""
    root = _memory_dir(
        tmp_path,
        repo,
        {"short.md": "---\nname: short\ndescription: Use -n 48 not auto here\n---\n"},
    )
    scan = H.scan(repo, projects_root=root)
    kept, duplicates = H.dedupe(scan.found, _state_with("Use -n 48, not auto"), _cfg(repo))
    assert [f.ident for f in kept] == ["M-harness-short"] and duplicates == []


def test_an_unrelated_memory_is_kept(repo, tmp_path):
    root = _memory_dir(tmp_path, repo, {"use-xdist.md": FACT})
    scan = H.scan(repo, projects_root=root)
    kept, duplicates = H.dedupe(
        scan.found, _state_with("LAN reviewers live in the machine-local file"), _cfg(repo)
    )
    assert [f.ident for f in kept] == ["M-harness-use-xdist"] and duplicates == []


def test_two_identical_files_in_one_scan_fold_to_one_line(repo, tmp_path):
    root = _memory_dir(tmp_path, repo, {"a.md": FACT, "b.md": FACT.replace("use-xdist", "copy")})
    scan = H.scan(repo, projects_root=root)
    kept, duplicates = H.dedupe(
        scan.found, _state_with("LAN reviewers live in the machine-local file"), _cfg(repo)
    )
    assert len(kept) == 1 and duplicates[0].where == "import" and duplicates[0].identical


def test_propose_scans_and_dedupes_in_one_call(repo, tmp_path):
    root = _memory_dir(tmp_path, repo, {"use-xdist.md": FACT})
    scan_result, kept, duplicates = H.propose(
        repo,
        projects_root=root,
        state=_state_with("Run tests with -n 48; serial takes 24 minutes and the gate times out"),
        cfg=_cfg(repo),
    )
    assert scan_result.found and kept == [] and duplicates[0].identical


def test_apply_records_the_memory_once(repo, tmp_path):
    root = _memory_dir(tmp_path, repo, {"use-xdist.md": FACT})
    scan = H.scan(repo, projects_root=root)
    log = EventLog(repo, "tester")
    first = H.apply(log, scan.found)
    assert any(a.startswith("remembered M-harness-use-xdist") for a in first)
    state = fold(log.read_all(), strict=False)
    memory = state.memories["M-harness-use-xdist"]
    assert memory.text == scan.found[0].body
    assert memory.source == "claude-memory/use-xdist.md"
    assert memory.tags == H.IMPORTED_TAGS
    assert memory.origin_at == "2026-09-29T11:00:00.000Z"
    second = H.apply(log, scan.found, state=state)
    assert any(a.startswith("already remembered") for a in second)
    recorded = [e for e in log.read_all() if e.kind == "memory.recorded"]
    assert len(recorded) == 1, "a re-run must not double the queue"


def test_two_files_with_one_id_are_flagged_and_the_second_is_refused(repo, tmp_path):
    """`a_b.md` and `a-b.md` slug to one id and the fold keeps one text per id; the
    report must not call both remembered (rubber_duck on a86e6f43)."""
    a = "---\nname: a\ndescription: First fact from file a\n---\n"
    b = "---\nname: b\ndescription: Second fact from file b\n---\n"
    root = _memory_dir(tmp_path, repo, {"a_b.md": a, "a-b.md": b})
    scan = H.scan(repo, projects_root=root)
    assert any("yield the same id" in p for p in scan.problems)
    log = EventLog(repo, "tester")
    out = H.apply(log, scan.found)
    assert any(a.startswith("remembered M-harness-a-b:") for a in out)
    assert any("uses this id with different text" in a for a in out)
    assert len([e for e in log.read_all() if e.kind == "memory.recorded"]) == 1


def test_a_forgotten_fact_is_refused_not_silently_revived(repo, tmp_path):
    """Re-recording clears the reason; the operator approving a plain 'remember' line
    does not know they are reviving it (rubber_duck on a86e6f43)."""
    root = _memory_dir(tmp_path, repo, {"use-xdist.md": FACT})
    scan = H.scan(repo, projects_root=root)
    state = State()
    state.memories["M-harness-use-xdist"] = Memory(
        id="M-harness-use-xdist", text=scan.found[0].body, forgotten="the box lost the GPUs"
    )
    log = EventLog(repo, "tester")
    out = H.apply(log, scan.found, state=state)
    assert any("was forgotten" in a for a in out)
    assert not list(log.read_all())


def test_apply_without_a_state_folds_the_log_itself(repo, tmp_path):
    """A careless caller must not double the queue (rubber_duck on a86e6f43)."""
    root = _memory_dir(tmp_path, repo, {"use-xdist.md": FACT})
    scan = H.scan(repo, projects_root=root)
    log = EventLog(repo, "tester")
    H.apply(log, scan.found)
    again = H.apply(log, scan.found)
    assert any(a.startswith("already remembered") for a in again)
    assert len([e for e in log.read_all() if e.kind == "memory.recorded"]) == 1


def test_apply_refuses_a_near_copy_the_caller_did_not_dedupe(repo, tmp_path):
    """The approved list normally comes from dedupe; a caller passing the raw scan must
    not slip a near-copy past the ask an add would raise (critic on 2c18772)."""
    long_fact = (
        "---\nname: xdist-long\ndescription: Run the suite with -n 48 because serial "
        "runs take 24 minutes and the unit_tests gate times out every time\n---\n"
    )
    root = _memory_dir(tmp_path, repo, {"xdist-long.md": long_fact})
    scan = H.scan(repo, projects_root=root)
    near = (
        "Run the suite with -n 48 because serial runs take 24 minutes and the unit_tests "
        "gate times out every single time"
    )
    log = EventLog(repo, "tester")
    out = H.apply(log, scan.found, state=_state_with(near), cfg=_cfg(repo))
    assert any("reads like M-existing" in a for a in out)
    assert not list(log.read_all())


def test_a_fact_over_the_memory_limit_is_refused_not_truncated(repo, tmp_path):
    fact = "---\nname: long\ndescription: " + "x" * 300 + "\n---\n"
    root = _memory_dir(tmp_path, repo, {"long.md": fact})
    scan = H.scan(repo, projects_root=root)
    log = EventLog(repo, "tester")
    out = H.apply(log, scan.found)
    assert any("over [memory] max_chars" in a for a in out)
    assert not list(log.read_all())


def test_render_shows_kept_duplicates_and_problems(repo, tmp_path):
    other = FACT.replace("use-xdist", "reviewers").replace(
        "Run tests with -n 48; serial takes 24 minutes and the gate times out",
        "LAN reviewers live in .ddflow/local",
    )
    root = _memory_dir(
        tmp_path,
        repo,
        {"MEMORY.md": "- [Gone](gone.md)\n", "use-xdist.md": FACT, "reviewers.md": other},
    )
    scan = H.scan(repo, projects_root=root)
    kept, duplicates = H.dedupe(
        scan.found,
        _state_with("Run tests with -n 48; serial takes 24 minutes and the gate times out"),
        _cfg(repo),
    )
    text = H.render(scan, kept, duplicates)
    assert "remember M-harness-reviewers" in text
    assert "from claude-memory/reviewers.md" in text
    assert "skip M-harness-use-xdist" in text
    assert "problem: MEMORY.md links gone.md" in text
