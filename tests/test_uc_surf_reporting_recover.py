"""`ddflow recover` and `ddflow rebuild` prose and exit codes (B-uc-surf-reporting).

The golden project has nothing to recover, so the situations are handed in: the sweep is
stubbed to return what a crashed agent leaves behind. Pinned on the hand-written handlers
and unchanged by their move onto the CLI executor.
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

from ddflow.services import leases as L
from ddflow.surfaces.cli import main

FOUND = [
    L.Recovery(
        "T1", "agent-a", "expired_lease", worktree="/wt/T1", advice="inspect it", salvageable=True
    ),
    L.Recovery("T2", "agent-b", "orphan_worktree", advice="remove it", salvageable=False),
    L.Recovery("T3", "agent-c", "stale_running", advice="resume it"),
]


@pytest.fixture
def cli(capsys):
    """One command in this process (the sweep is stubbed here, so not a subprocess)."""

    def run(repo, *argv):
        capsys.readouterr()
        code = main(["--repo", str(repo), "--agent", "t", *argv])
        cap = capsys.readouterr()
        return code, cap.out, cap.err

    return run


@pytest.fixture
def swept(repo, monkeypatch):
    assert run_cli(repo, "init")[0] == 0
    monkeypatch.setattr(L, "sweep", lambda log, cfg, repo, apply=False: list(FOUND))
    return repo


def test_recover_lists_every_situation_and_marks_the_ones_that_may_hold_work(swept, cli):
    code, out, err = cli(swept, "recover")
    assert (code, err) == (0, "")
    assert out == (
        "3 recoverable situation(s); 2 may contain work:\n\n"
        "!! T1  [expired_lease]  was: agent-a\n"
        "     worktree /wt/T1\n"
        "     inspect it\n\n"
        "   T2  [orphan_worktree]  was: agent-b\n"
        "     remove it\n\n"
        "!! T3  [stale_running]  was: agent-c\n"
        "     resume it\n\n"
        "Entries marked !! are NOT touched automatically. Inspect, salvage (or resume), "
        "then release.\n"
    )


def test_recover_json_is_the_found_list_and_a_clean_project_exits_2(swept, cli, monkeypatch):
    code, out, err = cli(swept, "--json", "recover")
    assert (code, err) == (0, "")
    assert [r["item"] for r in json.loads(out)] == ["T1", "T2", "T3"]
    monkeypatch.setattr(L, "sweep", lambda log, cfg, repo, apply=False: [])
    assert cli(swept, "recover") == (
        2,
        "Nothing to recover — no expired leases, no orphan worktrees.\n",
        "",
    )
    code, out, err = cli(swept, "--json", "recover")
    assert (code, json.loads(out), err) == (2, [], "")


def test_recover_can_be_narrowed_to_one_item_and_prints_no_trailer_without_work(swept, cli):
    code, out, _ = cli(swept, "recover", "--item", "T2")
    assert code == 0 and out == (
        "1 recoverable situation(s); 0 may contain work:\n\n"
        "   T2  [orphan_worktree]  was: agent-b\n"
        "     remove it\n\n"
    )


def test_rebuild_says_how_much_it_read_and_json_is_the_counts(repo, cli):
    assert run_cli(repo, "init")[0] == 0
    code, out, err = cli(repo, "rebuild")
    assert code == 0 and err == ""
    assert out.startswith("rebuilt index from ") and out.endswith("lessons)\n")
    code, out, err = cli(repo, "--json", "rebuild")
    assert code == 0 and {"events", "items"} <= set(json.loads(out))
