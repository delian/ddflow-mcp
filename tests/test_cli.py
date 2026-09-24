"""The CLI contract — exit codes above all, since that is what agents branch on."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


@pytest.fixture
def proj(repo):
    assert run_cli(repo, "init")[0] == OK
    run_cli(repo, "phase", "add", "P1", "--title", "Auth")
    run_cli(
        repo, "task", "add", "P1.T1", "--phase", "P1", "--title", "login", "--globs", "src/auth/*"
    )
    run_cli(
        repo,
        "task",
        "add",
        "P1.T2",
        "--phase",
        "P1",
        "--title",
        "logout",
        "--needs",
        "P1.T1",
        "--globs",
        "src/api/*",
    )
    return repo


def test_init_gitignores_the_derived_index(proj):
    """The index is ignored; the LOG is not.

    Asked of git itself rather than by reading patterns: a pattern test passes on a
    file that `git check-ignore` would actually ignore for some OTHER reason, and the
    question here is only ever what git does. (An earlier pattern-matching version of
    this test failed on `events.lock`, which starts with the same six letters as the
    directory that must stay tracked.)
    """
    import subprocess

    def ignored(rel: str) -> bool:
        return subprocess.run(["git", "-C", str(proj), "check-ignore", "-q", rel]).returncode == 0

    assert ignored(".orchard/index.db"), "the derived index must not be committed"
    assert ignored(".orchard/events.lock"), "the lock is machine-local"
    assert not ignored(".orchard/events/agent-a.jsonl"), (
        "the event log is the source of truth and MUST be committed"
    )


def test_init_sets_union_merge_on_the_log(proj):
    attrs = (proj / ".gitattributes").read_text()
    assert ".orchard/events/*.jsonl merge=union" in attrs


def test_next_exit_zero_when_ready(proj):
    code, out, _ = run_cli(proj, "next", "--phase", "P1")
    assert code == OK and "P1.T1" in out


def test_next_exit_two_when_nothing_actionable(proj):
    """Exit 2 is 'nothing to do' and MUST be distinguishable from exit 0."""
    run_cli(proj, "claim", "P1.T1", "--no-worktree")
    code, _, _ = run_cli(proj, "next", "--phase", "P1", agent="someone-else")
    assert code == NOTHING


def test_blocked_items_explain_themselves(proj):
    _code, out, _ = run_cli(proj, "--json", "next", "--phase", "P1")
    data = json.loads(out)
    blocked = {b["item"]: b for b in data["blocked"]}
    assert blocked["P1.T2"]["reason"] == "deps"
    assert "P1.T1" in blocked["P1.T2"]["waiting_on"]


def test_claim_by_a_second_agent_exits_three(proj):
    """A refusal names the holder, and names an alternative only when one exists.

    In this fixture P1.T2 depends on P1.T1, so once P1.T1 is held there is genuinely
    nothing else to offer — and the refusal must say nothing rather than invent
    something. (An earlier version suggested the PHASE here, which the task scheduler
    would then refuse.)
    """
    assert run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="a")[0] == OK
    code, _, err = run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="b")
    assert code == REFUSED
    assert "held by a" in err
    assert "You could take instead" not in err, "offered an alternative that does not exist"

    # Add a genuinely independent task; now the refusal must name it.
    run_cli(
        proj,
        "task",
        "add",
        "P1.T9",
        "--phase",
        "P1",
        "--title",
        "independent",
        "--globs",
        "src/other/*",
    )
    code, _, err = run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="b")
    assert code == REFUSED
    assert "You could take instead" in err and "P1.T9" in err


def test_complete_refuses_on_unmet_required_gates(proj):
    run_cli(proj, "claim", "P1.T1", "--no-worktree")
    code, _, err = run_cli(proj, "complete", "P1.T1")
    assert code == REFUSED
    assert "unit_tests" in err or "implement" in err


def test_complete_refuses_a_same_family_review_panel(proj):
    run_cli(proj, "claim", "P1.T1", "--no-worktree")
    for g in ("implement", "merge"):
        run_cli(proj, "gate", "record", "P1.T1", g, "--outcome", "passed")
    run_cli(
        proj,
        "gate",
        "record",
        "P1.T1",
        "unit_tests",
        "--outcome",
        "passed",
        "--evidence",
        "pytest: 12 passed",
        "--command",
        "pytest",
        "--exit-code",
        "0",
    )
    run_cli(
        proj,
        "gate",
        "record",
        "P1.T1",
        "rubber_duck",
        "--outcome",
        "passed",
        "--evidence",
        "reviewed",
        "--model",
        "claude-sonnet-5",
    )
    code, _, err = run_cli(proj, "complete", "P1.T1", "--model", "claude-opus-5")
    assert code == REFUSED and "independence not satisfied" in err.lower()
    run_cli(
        proj,
        "gate",
        "record",
        "P1.T1",
        "critic",
        "--outcome",
        "passed",
        "--evidence",
        "no findings",
        "--model",
        "gemini-2.5-pro",
    )
    assert run_cli(proj, "complete", "P1.T1", "--model", "claude-opus-5")[0] == OK


def test_gate_run_reports_unavailable_for_a_missing_tool(proj):
    (proj / ".orchard" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "no-such-tool-xyzzy"\n'
    )
    run_cli(proj, "claim", "P1.T1", "--no-worktree")
    code, out, _ = run_cli(proj, "gate", "run", "P1.T1", "unit_tests")
    assert code == NOTHING, "a tool that could not run is exit 2, not a failure"
    assert "UNAVAILABLE" in out


def test_gate_run_reports_failure_for_a_real_failure(proj):
    (proj / ".orchard" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "exit 1"\n')
    run_cli(proj, "claim", "P1.T1", "--no-worktree")
    assert run_cli(proj, "gate", "run", "P1.T1", "unit_tests")[0] == FAIL


def test_research_without_a_probe_cannot_be_confirmed(proj):
    code, _, err = run_cli(proj, "research", "--question", "is it fast?", "--verdict", "CONFIRMED")
    assert code == FAIL and "requires a --probe" in err
    assert (
        run_cli(
            proj,
            "research",
            "--question",
            "is it fast?",
            "--verdict",
            "CONFIRMED",
            "--probe",
            "bench.sh",
            "--probe-output",
            "1.2s",
        )[0]
        == OK
    )


def test_a_bug_cannot_be_closed_without_a_regression_test(proj):
    run_cli(proj, "bug", "found", "--id", "B1", "--summary", "off by one")
    code, _, err = run_cli(proj, "bug", "fixed", "B1")
    assert code == FAIL and "regression-test" in err
    assert (
        run_cli(
            proj, "bug", "fixed", "B1", "--regression-test", "tests/test_offbyone.py::test_boundary"
        )[0]
        == OK
    )


def test_brief_respects_its_token_budget(proj):
    for i in range(40):
        run_cli(
            proj, "lesson", "add", "--title", f"Lesson {i} " + "padding " * 40, "--rule", "x " * 200
        )
    code, out, _ = run_cli(proj, "--json", "brief", "--item", "P1.T1")
    assert code == OK
    data = json.loads(out)
    assert data["approx_tokens"] <= 1200 * 1.1, "the brief must honour its budget"


def test_doctor_detects_a_dependency_cycle(proj):
    run_cli(proj, "task", "add", "P1.TA", "--phase", "P1", "--needs", "P1.TB")
    run_cli(proj, "task", "add", "P1.TB", "--phase", "P1", "--needs", "P1.TA")
    code, out, _ = run_cli(proj, "doctor")
    assert code == FAIL and "cycle" in out


def test_doctor_detects_an_unknown_dependency(proj):
    run_cli(proj, "task", "add", "P1.TZ", "--phase", "P1", "--needs", "GHOST")
    code, out, _ = run_cli(proj, "doctor")
    assert code == FAIL and "GHOST" in out


def test_config_explains_every_knob(proj):
    code, out, _ = run_cli(proj, "config", "--explain")
    assert code == OK
    assert "lease.ttl_s" in out and "heartbeat" in out


def test_rebuild_is_idempotent(proj):
    a = run_cli(proj, "--json", "next", "--phase", "P1")[1]
    run_cli(proj, "rebuild")
    b = run_cli(proj, "--json", "next", "--phase", "P1")[1]
    assert json.loads(a)["ready"] == json.loads(b)["ready"]


def test_render_is_byte_stable(proj):
    run_cli(proj, "render")
    first = (proj / "docs" / "orchard" / "QUEUE.md").read_bytes()
    run_cli(proj, "render")
    assert (proj / "docs" / "orchard" / "QUEUE.md").read_bytes() == first


def test_a_failing_command_gate_is_recorded_not_crashed(proj):
    """The most ordinary event in the system must not take the tool down.

    `record` requires a reason for any non-pass outcome, and the command-gate path
    supplied none — so a test suite that simply failed raised ValueError. Found by the
    polyglot demo scenario. Mutation-verified: removing the synthesised reason in
    `cmd_gate` makes this red with 'must carry a --reason'.
    """
    (proj / ".orchard" / "gates.toml").write_text(
        "[gate.unit_tests]\ncommand = \"echo '3 tests, 1 failed' && exit 1\"\n"
    )
    run_cli(proj, "claim", "P1.T1", "--no-worktree")
    code, _out, err = run_cli(proj, "gate", "run", "P1.T1", "unit_tests")
    assert code == FAIL, err
    assert "must carry a --reason" not in err, "the gate crashed instead of recording"
    rec = json.loads(run_cli(proj, "--json", "show", "P1.T1")[1])["gates"]["unit_tests"]
    assert rec["outcome"] == "failed"
    assert "exited 1" in rec["reason"], rec["reason"]
    assert rec["evidence"]["exit"] == 1


def test_auto_generated_ids_do_not_collide_within_one_second(proj):
    """Second-resolution ids silently LOSE records.

    `f"L{int(time.time())}"` gives every item created in the same second the same id,
    and the fold then overwrites rather than appending — so the entry vanishes with no
    error anywhere. Measured before the fix: 7 lessons added in one second, 2 survived.
    Mutation-verified: restoring the timestamp id makes this red.
    """
    for i in range(8):
        assert (
            run_cli(
                proj,
                "lesson",
                "add",
                "--title",
                f"distinct lesson {i}",
                "--rule",
                f"rule number {i}",
            )[0]
            == OK
        )
    rows = json.loads(
        run_cli(proj, "--json", "lesson", "search", "distinct lesson", "--limit", "20")[1]
    )
    assert len(rows) == 8, f"only {len(rows)}/8 lessons survived — ids collided"
    assert len({r["id"] for r in rows}) == 8, "eight lessons but fewer distinct ids"

    # The same guarantee for the other two auto-id'd record types.
    for i in range(4):
        assert (
            run_cli(
                proj, "research", "--question", f"question number {i}", "--verdict", "THEORETICAL"
            )[0]
            == OK
        )
    assert (
        len(json.loads(run_cli(proj, "--json", "lesson", "search", "distinct", "--limit", "20")[1]))
        == 8
    )


def test_the_board_renders_the_CONFIGURED_pipeline_not_a_hardcoded_one(repo):
    """`render.board` re-typed the ten default gate ids instead of reading the config.

    A project that trimmed `gates.task_pipeline` to three gates got ten columns, seven
    permanently blank, under a caption naming gates it does not run — so a fully-passed
    task looked mostly unfinished. Found by roborev's duplication analysis.
    Mutation-verified: restoring the hardcoded tuple makes this red.
    """
    run_cli(repo, "init")
    (repo / ".orchard" / "config.toml").write_text(
        '[gates]\ntask_pipeline = ["implement", "unit_tests", "merge"]\n'
    )
    run_cli(repo, "phase", "add", "P1", "--title", "trimmed")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--title", "t")
    for g in ("implement", "unit_tests", "merge"):
        run_cli(repo, "gate", "record", "T1", g, "--outcome", "passed", "--evidence", "ok")
    board = run_cli(repo, "board")[1]
    row = next(ln for ln in board.splitlines() if "**T1**" in ln)
    column = row.split("|")[6].strip().strip("`")
    assert column == "xxx", f"expected 3 columns for a 3-gate pipeline, got {column!r}"
    assert "implement · unit_tests · merge." in board
    assert "rubber_duck" not in board, "the caption names gates this project does not run"
