"""`next` groups ready items for an orchestrator that places LANES by reading it.

`next` offered a flat, optimizer-ordered ready list, so an orchestrator had to learn
where to place a lane by TRIAL — trying claims and reading the refusals. Now, by default:

* the ready set is grouped by its ``wave:N`` bucket (unstamped last) and bugs then
  dependency-carrying items lead each bucket — ``--flat`` restores the flat list;
* each ready item's ``needs`` are shown inline besied its globs (``writes:``);
* ``co_location_hints`` clusters ready items that share a dependency or whose globs
  overlap, advising they be placed in ONE lane to minimize locking;
* ``--exclude-leased`` drops ready items whose globs overlap another agent's live
  lease, reported under ``excluded_leased`` (a second lane would contend).
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli

from ddflow.api import next_item


def _plan(repo, *argv, agent=""):
    code, out, err = run_cli(repo, "--json", "next", *argv, agent=agent)
    assert code in (0, 2), f"exit {code}\n{out}\n{err}"
    return json.loads(out)


def _shared_dep_phase(repo):
    """A phase with two ready tasks that both declare ``Shared`` as a dependency."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "M", "--title", "m")
    run_cli(repo, "task", "add", "Shared", "--title", "shared dep")
    run_cli(repo, "abandon", "Shared", "--reason", "handled elsewhere")
    run_cli(
        repo,
        "task",
        "add",
        "M.A",
        "--title",
        "task a",
        "--phase",
        "M",
        "--globs",
        "a.py",
        "--needs",
        "Shared",
    )
    run_cli(
        repo,
        "task",
        "add",
        "M.B",
        "--title",
        "task b",
        "--phase",
        "M",
        "--globs",
        "b.py",
        "--needs",
        "Shared",
    )


def test_ready_items_sharing_a_dep_are_one_co_location_cluster(repo):
    _shared_dep_phase(repo)
    plan = _plan(repo)
    ready = [r["id"] for r in plan["ready"]]
    assert ready == ["M.A", "M.B"], plan
    clustered = [h for h in plan["co_location_hints"] if set(h["items"]) >= {"M.A", "M.B"}]
    assert clustered, f"M.A and M.B share Shared but no cluster names them: {plan!r}"
    assert "Shared" in clustered[0]["shared_deps"], clustered[0]


def test_ready_items_not_sharing_a_dep_make_no_cluster(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "M", "--title", "m")
    run_cli(repo, "task", "add", "M.A", "--title", "a", "--phase", "M", "--globs", "a.py")
    run_cli(repo, "task", "add", "M.B", "--title", "b", "--phase", "M", "--globs", "b.py")
    plan = _plan(repo)
    assert plan["co_location_hints"] == [], plan


def test_ready_rows_name_needs_and_wave_in_json(repo):
    _shared_dep_phase(repo)
    plan = _plan(repo)
    by_id = {r["id"]: r for r in plan["ready"]}
    assert by_id["M.A"]["needs"] == ["Shared"], by_id["M.A"]
    assert by_id["M.A"]["wave"] is None, "no wave:N tag is stamped in the suite (auto_stamp off)"


def test_exclude_leased_drops_an_item_whose_globs_overlap_another_agents_lease(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_SCHEDULE_READY_POLICY", "deps_only")
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "M", "--title", "m")
    run_cli(repo, "task", "add", "M.A", "--title", "a", "--phase", "M", "--globs", "shared.py")
    run_cli(repo, "task", "add", "M.B", "--title", "b", "--phase", "M", "--globs", "shared.py")
    run_cli(repo, "claim", "M.A", "--no-worktree", agent="lane1")

    # deps_only surfaces M.B ready, despite lane1's overlapping lease on shared.py.
    shown = next_item(Path(repo), agent="lane2")
    assert "M.B" in [r["id"] for r in shown.data["ready"]], shown.data
    assert shown.data["excluded_leased"] == [], shown.data

    # exclude_leased drops it and reports why.
    filtered = next_item(Path(repo), agent="lane2", exclude_leased=True)
    assert "M.B" not in [r["id"] for r in filtered.data["ready"]], filtered.data
    assert any(e["item"] == "M.B" for e in filtered.data["excluded_leased"]), filtered.data


def test_human_text_shows_needs_inline_and_co_location(repo):
    _shared_dep_phase(repo)
    code, out, _err = run_cli(repo, "next")
    assert code == 0, out
    assert "needs: Shared" in out, out
    assert "Co-locate" in out, out


def test_two_unstamped_items_are_one_unstamped_bucket_not_two(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "M", "--title", "m")
    run_cli(repo, "task", "add", "M.A", "--title", "a", "--phase", "M", "--globs", "a.py")
    run_cli(repo, "task", "add", "M.B", "--title", "b", "--phase", "M", "--globs", "b.py")
    code, out, _err = run_cli(repo, "next")
    assert code == 0, out
    assert out.count("unstamped") == 1, out

