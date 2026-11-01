"""B-upgrade.4-apply: the CLI, --json and MCP wire of `ddflow upgrade --apply`.

Shared fixture and helpers: tests/_upgrade_apply_support.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from _upgrade_apply_support import (
    cli,
    tree,
)

import ddflow.surfaces.cli  # noqa: F401 -- registers the command table the reference check needs
from ddflow.infra.log import EventLog

#: Read by `ddflow tests --item` (B81cf6e7dfd): the plan is built from the unreleased manifest
#: fragments, data no import reaches, so a new knob fragment must select these tests.
GOVERNS = ("ddflow/templates/upgrade/**", "ddflow/services/upgrade_manifest.py")


# -- B-upgrade.4-apply.1b-wire: the CLI, --json and MCP --------------------------------------


def test_the_cli_apply_does_the_plan_saves_the_originals_and_a_second_run_is_a_noop(
    old: Path,
) -> None:
    code, out, err = cli(old, "upgrade", "--apply", "hooks")

    assert code == 0, out + err
    assert "[applied] hooks:missing:claude:session-start" in out
    assert "backup:" in out and ".ddflow/backups/" in out and "git diff" in out
    snapshot = tree(old)

    code2, out2, _ = cli(old, "upgrade", "--apply", "hooks")

    assert code2 == 0 and "nothing to apply" in out2
    assert tree(old) == snapshot, "applying twice leaves an identical tree"


def test_the_cli_exits_3_for_an_item_that_needs_confirmation_and_names_the_flag(old: Path) -> None:
    code, out, err = cli(old, "upgrade", "--apply", "config")

    assert code == 3, out + err
    assert "--confirm worktree.max_parallel" in out
    assert "need the operator's confirmation" in err


def test_confirm_needs_a_reason_and_with_both_the_change_is_made_and_recorded(old: Path) -> None:
    refused = cli(old, "upgrade", "--apply", "config", "--confirm", "worktree.max_parallel")
    assert refused[0] == 3 and "--reason" in refused[2]
    assert not [e for e in EventLog(old, "upgrader").read_all() if e.data.get("confirmed")]

    code, out, err = cli(
        old,
        "upgrade",
        "--apply",
        "config",
        "--confirm",
        "worktree.max_parallel",
        "--reason",
        "the new default fits",
    )

    assert code == 0, out + err
    event = [e for e in EventLog(old, "upgrader").read_all() if e.kind == "upgrade.applied"][-1]
    assert event.data["confirmed"] == {"worktree.max_parallel": "the new default fits"}


def test_plan_and_apply_together_are_refused(old: Path) -> None:
    code, _out, err = cli(old, "upgrade", "--plan", "--apply")

    assert code == 3 and "not both" in err


def test_an_unknown_category_is_a_failure_that_names_the_known_ones(old: Path) -> None:
    code, _out, err = cli(old, "upgrade", "--apply", "hooks,nope")

    assert code == 1 and "unknown upgrade category" in err and "instructions" in err


def test_json_carries_the_plan_that_is_left_and_what_the_apply_did(old: Path) -> None:
    code, out, _ = cli(old, "--json", "upgrade", "--apply", "hooks")

    body = json.loads(out)
    assert code == 0
    assert set(body) == {*UP_FIELDS, "applied"}
    assert body["categories"]["hooks"] == [] and body["total"] > 0
    assert body["applied"]["backup"] and body["applied"]["results"]
    assert body["applied"]["to"] == "0.0.0", "a partial apply keeps the project's version"


UP_FIELDS = ("schema", "running", "project_version", "up_to_date", "total", "categories")


def test_the_plan_json_is_unchanged_and_has_no_applied(old: Path) -> None:
    code, out, _ = cli(old, "--json", "upgrade")

    assert code == 1 and set(json.loads(out)) == set(UP_FIELDS)


def test_over_mcp_apply_confirm_and_reason_work_like_the_cli(old: Path) -> None:
    from ddflow.surfaces.mcp import Server

    def call(args: dict) -> dict:
        reply = Server(old).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_upgrade", "arguments": args},
            }
        )
        return reply["result"]

    needs = call({"apply": "config"})
    assert needs["_meta"]["exit"] == 3

    done = call(
        {
            "apply": "config",
            "confirm": ["worktree.max_parallel"],
            "reason": "the new default fits",
        }
    )

    assert done["_meta"]["exit"] == 0
    body = json.loads(done["content"][0]["text"])
    assert body["applied"]["confirmed"] == {"worktree.max_parallel": "the new default fits"}


def test_over_mcp_an_explicit_plan_true_with_apply_is_refused_like_the_cli(old: Path) -> None:
    from ddflow.surfaces.mcp import Server

    reply = Server(old).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_upgrade", "arguments": {"plan": True, "apply": "hooks"}},
        }
    )["result"]

    assert reply["_meta"]["exit"] == 3
    assert not (old / ".claude" / "settings.json").exists(), "nothing was applied"


def test_a_refusal_on_the_cli_json_path_still_says_why_on_stderr(old: Path) -> None:
    code, _out, err = cli(old, "--json", "upgrade", "--plan", "--apply")

    assert code == 3 and "not both" in err
