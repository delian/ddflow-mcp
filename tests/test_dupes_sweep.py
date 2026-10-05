"""B-dupes-sweep: `ddflow dupes` (near-duplicate pairs already in the log) and
`ddflow link` (settle one), with `dedupe_sweep` and `doctor` reading the same sweep.

Decision D-no-duplicates; research R-dedupe-matchers (why a score is a prompt to LOOK,
not a verdict) and R-dedupe-evalset (the labelled fixture). The acceptance the task set:
on ddflow's log the sweep finds B203 ~ B-semantic-recall; on home-simulator's log it
finds >=60 of the 86 duplicated LS-* lessons; and a pair marked `distinct` never returns.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import knowledge as K
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Two reports of one defect in different words: a clear pair, well above the show floor.
BUG_A = "the parser drops the trailing token when the line is empty"
BUG_B = "parser drops a trailing token on an empty line"
LESSON = (
    "Always rebase an agent branch onto the current main before merging it, otherwise "
    "the merge silently reverts work another agent landed in the meantime."
)


def _records(rel: str) -> list[dict]:
    text = (FIXTURES / rel).read_text("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _found_ls(rows: list[dict]) -> set[str]:
    return {x for r in rows for x in (r["a"], r["b"]) if x.startswith("LS-")}


# -- the acceptance set ------------------------------------------------------------------


def test_the_sweep_finds_B203_as_a_duplicate_of_B_semantic_recall():
    """On ddflow's own log (the redacted corpus snapshot, B-dedupe-evalset): the engine
    pairs B203 with B-semantic-recall. The fixture carries every record the sweep weighs,
    so this pins the SCORES; the removed-record RECOVERY (`_sweep_records`) is pinned end
    to end by `test_a_removed_item_is_still_swept_end_to_end`."""
    floor = Config.load().dedupe.show_floor
    rows = K.pair_records(_records("dedupe/corpus.jsonl"), floor=floor)
    assert any({r["a"], r["b"]} == {"B203", "B-semantic-recall"} for r in rows), (
        "B203 ~ B-semantic-recall missing from the sweep"
    )


def test_the_sweep_finds_at_least_60_of_home_simulators_86_LS_lessons():
    """home-simulator's 86 imported `LS-*` summary lessons against its own `L*` lessons.

    The fixture keeps each lesson's ID and TITLE (their bodies are long project text and
    are not redistributed here); the full-text probe found 79 distinct LS lessons, and
    titles alone still clear the bar of 60 -- so the test is a real ratchet, not one
    tuned to the fixture.
    """
    records = _records("dupes/home_simulator_lesson_titles.jsonl")
    assert sum(1 for r in records if r["id"].startswith("LS-")) == 86
    rows = K.pair_records(records, floor=Config.load().dedupe.show_floor)
    assert len(_found_ls(rows)) >= 60, sorted(_found_ls(rows))


# -- the command, end to end -------------------------------------------------------------


def _two_bugs(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", BUG_A, "--no-task")
    run_cli(repo, "bug", "found", "--id", "B2", "--summary", BUG_B, "--no-task")


def test_dupes_lists_a_near_duplicate_pair_and_json_carries_it(repo):
    _two_bugs(repo)
    code, out, err = run_cli(repo, "dupes")
    assert code == 0, err
    assert "B1" in out and "B2" in out
    code, out, _ = run_cli(repo, "--json", "dupes")
    body = json.loads(out)
    assert code == 0 and body["count"] >= 1
    assert any({p["a"], p["b"]} == {"B1", "B2"} for p in body["pairs"])
    assert body["floor"] == Config.load().dedupe.show_floor


def test_a_pair_marked_distinct_never_returns(repo):
    _two_bugs(repo)
    assert run_cli(repo, "dupes")[0] == 0
    code, out, err = run_cli(repo, "link", "B2", "--distinct", "B1")
    assert code == 0, err
    code, out, err = run_cli(repo, "dupes")
    assert code == 2, f"a dismissed pair came back: {out}{err}"


def test_a_related_link_also_settles_the_pair(repo):
    _two_bugs(repo)
    assert run_cli(repo, "link", "B2", "--related", "B1")[0] == 0
    assert run_cli(repo, "dupes")[0] == 2


def test_open_only_drops_a_closed_record_but_the_full_sweep_keeps_it(repo):
    _two_bugs(repo)
    run_cli(repo, "bug", "invalid", "B1", "--reason", "not a real bug")
    assert run_cli(repo, "dupes", "--open-only")[0] == 2, "a closed record was swept as live"
    assert run_cli(repo, "dupes")[0] == 0, "the closed record is out of the history sweep"


HOOK_TEXT = (
    "The commit hook refuses a commit that touches a path no live lease of yours covers, "
    "so widen the item's globs before writing outside them."
)


def test_a_removed_item_is_still_swept_end_to_end(repo):
    """The recovery `_sweep_records` exists for, reached through the real command.

    `similar_records` drops a REMOVED item; the sweep adds it back so a pair whose
    duplicate was taken back (B203's case) is still found. A test that feeds
    `pair_records` a fixture bypasses `_sweep_records` entirely and cannot see this.
    """
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "P")
    run_cli(
        repo,
        "task",
        "add",
        "T1",
        "--phase",
        "P1",
        "--title",
        "Hook refuses uncovered paths",
        "--body",
        HOOK_TEXT,
    )
    run_cli(
        repo,
        "task",
        "add",
        "T2",
        "--phase",
        "P1",
        "--title",
        "Commit hook path not in a lease",
        "--body",
        HOOK_TEXT,
    )
    assert run_cli(repo, "dupes")[0] == 0
    run_cli(repo, "remove", "T1", "--reason", "superseded by T2")
    assert run_cli(repo, "dupes", "--open-only")[0] == 2, "a removed item was swept as live"
    code, out, err = run_cli(repo, "dupes")
    assert code == 0, f"the removed item dropped out of the sweep: {out}{err}"
    assert "T1" in out and "T2" in out
    # A pair `dupes` shows must be settleable, removed item included.
    code, out, err = run_cli(repo, "link", "T1", "--distinct", "T2", "--reason", "different work")
    assert code == 0, err
    assert run_cli(repo, "dupes")[0] == 2, "a pair involving a removed item could not be settled"


MEMORY_TEXT = "vLLM serves the fleet on ports 8000 to 8007 and the load balancer fronts them."


def test_a_forgotten_memory_is_still_swept_end_to_end(repo):
    """The memory half of `_sweep_records`: a forgotten memory is out of the index, but
    the sweep must still weigh it, or a pair two agents recorded and one retracted is
    never offered."""
    run_cli(repo, "init")
    run_cli(repo, "memory", "add", MEMORY_TEXT, "--id", "M1")
    run_cli(repo, "memory", "add", MEMORY_TEXT, "--id", "M2")
    assert run_cli(repo, "dupes", "--kind", "memory")[0] == 0
    run_cli(repo, "memory", "forget", "M1", "--reason", "wrong machine")
    assert run_cli(repo, "dupes", "--kind", "memory", "--open-only")[0] == 2
    assert run_cli(repo, "dupes", "--kind", "memory")[0] == 0, "the forgotten memory vanished"


def test_linking_two_lessons_merges_them(repo):
    """B198's `lesson merge`, through the one mechanism that retires a lesson: the target
    keeps both texts' tags and `seen_in`, and the duplicate is superseded by it."""
    run_cli(repo, "init")
    run_cli(
        repo,
        "lesson",
        "add",
        "--id",
        "L1",
        "--title",
        "Rebase before merging",
        "--rule",
        LESSON,
        "--tags",
        "git,merge",
        "--seen-in",
        "docs/a.md",
    )
    run_cli(
        repo,
        "lesson",
        "add",
        "--id",
        "L2",
        "--title",
        "Rebase agents first",
        "--rule",
        LESSON,
        "--tags",
        "vcs",
        "--seen-in",
        "docs/b.md",
    )
    code, _out, err = run_cli(repo, "link", "L2", "--duplicate-of", "L1")
    assert code == 0, err
    assert run_cli(repo, "dupes")[0] == 2, "a merged lesson pair is still unsettled"
    st = fold(EventLog(repo).read_all())
    assert st.lessons["L2"].superseded_by == "L1"
    assert set(st.lessons["L1"].tags) == {"git", "merge", "vcs"}
    assert set(st.lessons["L1"].seen_in) == {"docs/a.md", "docs/b.md"}
    assert st.lessons["L2"].title == "Rebase agents first", "the duplicate is kept, not deleted"


def test_link_refuses_an_unknown_relation_and_an_unknown_record(repo):
    _two_bugs(repo)
    assert run_cli(repo, "link", "B2", "--related", "NOPE")[0] == 3
    # argparse refuses a bad relation flag before the API sees it; the API's own guard is
    # exercised directly.
    from ddflow.api import knowledge

    out = knowledge.link_record(repo, "B2", "sibling-of", "B1")
    assert out.exit == 1 and "unknown relation" in out.reason


def test_doctor_counts_the_unsettled_pairs(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", BUG_A, "--no-task")
    # The second is a duplicate, so it needs the answer even though we want it filed.
    run_cli(repo, "bug", "found", "--id", "B2", "--summary", BUG_B, "--no-task", "--new")
    _code, out, _err = run_cli(repo, "doctor")
    assert "unsettled near-duplicate pair" in out
    assert "ddflow dupes" in out


def test_the_dedupe_sweep_prompt_runs_dupes_open_only():
    from ddflow.services import prompts as P

    text = P.render(P.resolve_command("dedupe_sweep"), test_gates=[])
    assert "dupes --open-only" in text
    assert "cadence --ran dedupe_sweep" in text
    assert "{{" not in text and "{%" not in text


# -- over MCP ----------------------------------------------------------------------------


def _call(repo: Path, name: str, arguments: dict) -> dict:
    from ddflow.surfaces.mcp import Server

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    return reply["result"]


def test_link_over_mcp_settles_a_pair(repo):
    _two_bugs(repo)
    result = _call(repo, "ddflow_dupes", {})
    body = json.loads(result["content"][0]["text"])
    assert any({p["a"], p["b"]} == {"B1", "B2"} for p in body["pairs"])

    settled = _call(
        repo,
        "ddflow_link",
        {"subject": "B2", "relation": "distinct", "target": "B1"},
    )
    assert not settled.get("isError"), settled
    after = _call(repo, "ddflow_dupes", {})
    assert json.loads(after["content"][0]["text"])["count"] == 0
