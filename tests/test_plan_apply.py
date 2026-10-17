"""`ddflow plan apply` / `plan confirm`: a checked plan is filed PROPOSED, atomically
(B-plan-apply-and-the-rest, D-sdd-plan-modes)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli

PLAN = """
[a]
title = "first"
globs = ["src/a.py"]
acceptance = "a works"

[b]
title = "second"
needs = ["a"]
globs = ["src/b.py"]
acceptance = "b works"
"""


def _write(tmp: Path, text: str = PLAN) -> str:
    p = tmp / "plan.toml"
    p.write_text(text)
    return str(p)


def _state(repo: Path, item: str) -> dict:
    code, out, err = run_cli(repo, "--json", "show", item)
    assert code == 0, out + err
    return json.loads(out)


def _hash(repo: Path, plan: str, plan_id: str = "p1") -> str:
    code, out, err = run_cli(repo, "--json", "plan", "apply", plan, "--plan-id", plan_id)
    assert code == 0, out + err
    return json.loads(out)["hash"]


def test_apply_is_a_dry_run_by_default(repo, tmp_path):
    plan = _write(tmp_path)

    code, out, err = run_cli(repo, "plan", "apply", plan)

    assert code == 0, out + err
    assert "dry run" in out and "would file a" in out
    assert run_cli(repo, "show", "a")[0] != 0  # nothing was filed


def test_apply_refuses_a_plan_with_findings_and_writes_nothing(repo, tmp_path):
    plan = _write(tmp_path, PLAN.replace('acceptance = "b works"', ""))

    code, out, err = run_cli(repo, "plan", "apply", plan, "--apply", "--hash", "x")

    assert code == 1, out + err
    assert "no_acceptance" in out + err
    assert run_cli(repo, "show", "a")[0] != 0


def test_apply_refuses_a_stale_hash(repo, tmp_path):
    plan = _write(tmp_path)
    digest = _hash(repo, plan)
    Path(plan).write_text(PLAN.replace("first", "first, edited"))

    code, out, err = run_cli(repo, "plan", "apply", plan, "--apply", "--hash", digest)

    assert code == 3, out + err
    assert "stale" in out + err
    assert run_cli(repo, "show", "a")[0] != 0


def test_apply_without_a_hash_is_refused(repo, tmp_path):
    code, out, err = run_cli(repo, "plan", "apply", _write(tmp_path), "--apply")
    assert code == 3, out + err


def test_applied_tasks_are_proposed_labelled_and_not_ready(repo, tmp_path):
    plan = _write(tmp_path)
    digest = _hash(repo, plan)

    code, out, err = run_cli(
        repo, "plan", "apply", plan, "--apply", "--hash", digest, "--plan-id", "p1"
    )

    assert code == 0, out + err
    for iid in ("a", "b"):
        shown = _state(repo, iid)
        assert shown["state"] == "blocked"
        assert "plan:p1" in shown["tags"]
        assert "proposed by plan:p1" in shown["blocked_reason"]
    code, out, _ = run_cli(repo, "--json", "next")
    assert "a" not in [r["id"] for r in json.loads(out).get("ready", [])]


def test_confirm_needs_consent_then_releases_exactly_the_plans_tasks(repo, tmp_path):
    plan = _write(tmp_path)
    digest = _hash(repo, plan)
    run_cli(repo, "plan", "apply", plan, "--apply", "--hash", digest, "--plan-id", "p1")
    run_cli(repo, "task", "add", "other", "--title", "parked by hand", "--new")
    run_cli(repo, "block", "other", "--reason", "vendor")

    code, out, err = run_cli(repo, "plan", "confirm", "p1")
    assert code == 3, out + err
    assert _state(repo, "a")["state"] == "blocked"

    code, out, err = run_cli(repo, "plan", "confirm", "p1", "--yes", "--reason", "reviewed")
    assert code == 0, out + err
    assert _state(repo, "a")["state"] == "open"
    assert _state(repo, "b")["state"] == "open"
    assert _state(repo, "other")["state"] == "blocked"
    assert run_cli(repo, "plan", "confirm", "p1", "--yes", "--reason", "again")[0] == 2


def test_a_run_killed_half_way_is_finished_by_running_it_again(repo, tmp_path):
    """What a killed run left is this plan's own: the rest is filed, nothing twice."""
    half = _write(tmp_path, PLAN.split("[b]", maxsplit=1)[0])
    run_cli(repo, "plan", "apply", half, "--apply", "--hash", _hash(repo, half), "--plan-id", "p1")
    full = _write(tmp_path)

    code, out, err = run_cli(
        repo,
        "--json",
        "plan",
        "apply",
        full,
        "--apply",
        "--hash",
        _hash(repo, full),
        "--plan-id",
        "p1",
    )

    assert code == 0, out + err
    assert json.loads(out)["filed"] == ["b"]
    assert _state(repo, "b")["state"] == "blocked"


def test_a_plan_item_that_reads_like_a_queued_one_is_a_duplicate_finding(
    repo, tmp_path, monkeypatch
):
    """The duplicate hunt: `plan check` asks the add-time check about every plan item."""
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
    title = "Rotate the credentials of the staging database every thirty days"
    body = "Rotation job for staging database credentials, thirty day interval, alert on failure."
    assert run_cli(repo, "task", "add", "queued", "--title", title, "--body", body, "--new")[0] == 0
    plan = _write(
        tmp_path,
        f'[dup]\ntitle = "{title}"\nbody = "{body}"\nglobs = ["src/rot.py"]\nacceptance = "done"\n',
    )

    code, out, err = run_cli(repo, "--json", "plan", "check", plan)

    assert code == 1, out + err
    found = [f for f in json.loads(out)["findings"] if f["kind"] == "duplicate"]
    assert found and found[0]["ids"][:2] == ["dup", "queued"]


def test_an_id_already_in_the_queue_is_refused_not_overwritten(repo, tmp_path):
    run_cli(repo, "task", "add", "a", "--title", "mine", "--new")
    plan = _write(tmp_path)

    code, out, err = run_cli(repo, "plan", "apply", plan)

    assert code == 3, out + err
    assert _state(repo, "a")["title"] == "mine"
