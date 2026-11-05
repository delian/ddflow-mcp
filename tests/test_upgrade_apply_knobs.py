"""B-upgrade.4-apply: the [upgrade] backup / backup_keep / config_changes knobs and settled items.

Shared fixture and helpers: tests/_upgrade_apply_support.py.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from _upgrade_apply_support import (
    backups,
    cli,
    go,
    ids,
    set_knob,
    upgrades,
)
from helpers import upgrade_plan as plan

import ddflow.surfaces.cli  # noqa: F401 -- registers the command table the reference check needs
from ddflow.api._base import _load
from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services import upgrade_apply as UA
from ddflow.services import upgrade_plan as UP

#: Read by `ddflow tests --item` (B81cf6e7dfd): the plan is built from the unreleased manifest
#: fragments, data no import reaches, so a new knob fragment must select these tests.
GOVERNS = ("ddflow/templates/upgrade/**", "ddflow/services/upgrade_manifest.py")


# -- B-upgrade.4-apply.1c-knobs: [upgrade] backup / backup_keep / config_changes --------------


def test_the_knob_defaults_are_the_decided_ones() -> None:
    up = Config().upgrade

    assert (up.backup, up.backup_keep, up.config_changes) == ("local", 10, "agent")


def test_backup_none_in_the_config_skips_the_copy(old: Path) -> None:
    set_knob(old, "upgrade.backup", "none")

    code, out, _ = cli(old, "upgrade", "--apply", "hooks")

    assert code == 0 and "backup:" not in out and backups(old) == []


def test_the_backup_flag_overrides_the_knob_for_one_run(old: Path) -> None:
    set_knob(old, "upgrade.backup", "none")

    code, _out, _ = cli(old, "upgrade", "--apply", "hooks", "--backup", "local")

    assert code == 0 and len(backups(old)) == 1


def test_backup_keep_removes_the_oldest_beyond_it(old: Path) -> None:
    set_knob(old, "upgrade.backup_keep", "1")
    assert cli(old, "upgrade", "--apply", "hooks")[0] == 0
    (first,) = backups(old)

    assert cli(old, "upgrade", "--apply", "instructions")[0] == 0

    left = backups(old)
    assert len(left) == 1 and left != [first], "the older backup was pruned, the new one kept"


def test_backup_keep_zero_keeps_every_backup(old: Path) -> None:
    set_knob(old, "upgrade.backup_keep", "0")
    assert cli(old, "upgrade", "--apply", "hooks")[0] == 0
    assert cli(old, "upgrade", "--apply", "instructions")[0] == 0

    assert len(backups(old)) == 2


def test_prune_touches_only_directories_that_are_backups(tmp_path: Path) -> None:
    from ddflow.services import backups as BK

    root = tmp_path / ".ddflow" / "backups"
    for name in ("20260101T000000Z-a", "20260102T000000Z-b", "20260103T000000Z-c"):
        (root / name).mkdir(parents=True)
        (root / name / BK.MANIFEST).write_text("{}")
    (root / "notes").mkdir()
    (root / "notes" / "keep.txt").write_text("mine")

    removed = BK.prune(tmp_path, 2)

    assert removed == ["20260101T000000Z-a"] and (root / "notes" / "keep.txt").exists()
    assert BK.prune(tmp_path, 0) == [] and BK.prune(tmp_path / "nowhere", 1) == []


def test_config_changes_ask_makes_the_cli_refuse_an_unset_knob_too(old: Path) -> None:
    set_knob(old, "upgrade.config_changes", "ask")

    code, out, _err = cli(old, "upgrade", "--apply", "config")

    assert code == 3
    assert "[refused] knob_changed:worktree.root" in out
    assert "config_changes = ask" in out


def test_config_changes_operator_with_a_confirm_applies_it(old: Path) -> None:
    set_knob(old, "upgrade.config_changes", "operator")

    code, out, _ = cli(
        old,
        "upgrade",
        "--apply",
        "config",
        "--confirm",
        "worktree.root",
        "--confirm",
        "worktree.max_parallel",
        "--confirm",
        "session.redact_patterns",
        # `operator` refuses every default change of a knob the old project already had:
        # log.snapshot is not here because the fixture predates it, so it nets to an addition
        "--confirm",
        "log.max_cached_events",
        "--reason",
        "the operator accepted the new defaults",
    )

    assert "[acknowledged] knob_changed:worktree.root" in out
    assert "[acknowledged] knob_changed:worktree.max_parallel" in out
    assert "[acknowledged] knob_changed:log.max_cached_events" in out
    assert "[refused] knob_changed:worktree.root" not in out
    assert "[refused] knob_changed:worktree.max_parallel" not in out
    assert code == 0, out
    # a knob at its default is acknowledged, never written out as if the operator had chosen it
    assert "worktree.root" not in (old / ".ddflow" / "config.toml").read_text()


def test_the_plan_marks_unset_knob_changes_as_the_operators_only_under_operator(old: Path) -> None:
    def action(key: str) -> str:
        return next(i["action"] for i in plan(old)["categories"]["config"] if i["key"] == key)

    assert action("worktree.root") == UP.AGENT
    set_knob(old, "upgrade.config_changes", "ask")
    assert action("worktree.root") == UP.AGENT, "ask: the agent asks, the plan is unchanged"
    set_knob(old, "upgrade.config_changes", "operator")
    assert action("worktree.root") == UP.OPERATOR
    # a new knob is only news, whatever the policy
    assert action("dedupe.min_words") == UP.NOTE


def test_a_prune_that_could_not_remove_a_backup_does_not_claim_it_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    from ddflow.services import backups as BK

    root = tmp_path / ".ddflow" / "backups"
    for name in ("20260101T000000Z-a", "20260102T000000Z-b"):
        (root / name).mkdir(parents=True)
        (root / name / BK.MANIFEST).write_text("{}")
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)  # the removal is refused

    assert BK.prune(tmp_path, 1) == []


def test_a_prune_that_cannot_read_the_directory_is_not_an_apply_failure(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.services import backups as BK

    real = Path.iterdir

    def deny(self: Path):
        if self.name == "backups":
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", deny)

    assert BK.prune(old, 1) == []
    code, out, _ = cli(old, "upgrade", "--apply", "hooks")
    assert code == 0 and "[applied] hooks:missing" in out


def test_a_bad_backup_value_still_makes_the_backup(old: Path) -> None:
    (old / ".ddflow" / "config.toml").write_text(
        (old / ".ddflow" / "config.toml").read_text() + '\n[upgrade]\nbackup = "snapshotty"\n'
    )

    assert Config.load(old).upgrade.backup == "local"
    code, out, _ = cli(old, "upgrade", "--apply", "hooks")

    assert code == 0 and "backup:" in out and len(backups(old)) == 1


def test_one_policy_serves_the_plan_and_the_apply(old: Path) -> None:
    set_knob(old, "upgrade.config_changes", "ask")

    plan_action = next(
        i["action"] for i in plan(old)["categories"]["config"] if i["key"] == "worktree.root"
    )
    out = go(old, "config")

    # under `ask` the plan leaves the change to the agent: only apply's own read of the knob
    # makes it wait for --confirm, so a second policy source in apply() would fail here
    assert plan_action == UP.AGENT
    assert any(r["key"] == "worktree.root" and r["status"] == "refused" for r in out["results"])


def test_confirming_a_pinned_knob_keeps_the_pin_instead_of_adopting_the_new_default(
    old: Path,
) -> None:
    """Bc7a97b0e2c: `--confirm KEY` on a knob the project pins overwrote the pin."""
    key = "worktree.max_parallel"
    item = next(i for i in plan(old)["categories"]["config"] if i["key"] == key)
    pinned = Config.load(old).worktree.max_parallel
    assert item["set_by"] and pinned != item["new"]
    text = (old / ".ddflow" / "config.toml").read_text()

    out = go(old, "config", confirm={key: "keep my pin"})

    row = next(r for r in out["results"] if r["key"] == key)
    assert row["status"] == "acknowledged" and "kept" in row["detail"]
    assert f"ddflow config --set {key} {item['new']}" in row["detail"]
    assert "keeps this project's value" in item["fix"]
    assert Config.load(old).worktree.max_parallel == pinned
    assert (old / ".ddflow" / "config.toml").read_text() == text


def test_the_adopt_command_for_a_pinned_knob_is_not_truncated(old: Path) -> None:
    _, cfg, _ = _load(old, "upgrader")
    key = "worktree.max_parallel"
    item = next(i for i in plan(old)["categories"]["config"] if i["key"] == key)
    long_value = "v" * 100
    status, detail = UA._apply_config(old, cfg, {**item, "new": long_value}, "upgrader")
    assert status == "acknowledged" and json.dumps(long_value) in detail


def test_an_acknowledged_item_leaves_the_plan_while_a_refused_one_stays(old: Path) -> None:
    """upgrade-plan-never-empties: acknowledged items came back in every plan, and every
    re-apply acknowledged them again and appended another full batch to the event log."""
    first = go(old, "all")
    acked = set(ids(first, "acknowledged"))
    refused = set(ids(first, "refused"))
    assert acked and refused

    left = {i["id"] for c in plan(old)["categories"].values() for i in c}
    assert not acked & left, sorted(acked & left)
    assert refused <= left

    n = len(upgrades(old))
    again = go(old, "all")
    assert not ids(again, "acknowledged"), again["text"]
    assert len(upgrades(old)) == n  # nothing new to record: no duplicate batch


def test_confirming_the_last_refusal_advances_the_stamp_after_earlier_acknowledgements(
    old: Path,
) -> None:
    go(old, "all")
    keys = {i["key"]: "ok" for i in plan(old)["categories"]["config"] if i["action"] == UP.OPERATOR}
    go(old, "all", confirm=keys)
    final = plan(old)
    assert final["project_version"] == final["running"]
    assert final["up_to_date"] or not final["categories"]["config"]


def test_a_plan_emptied_by_earlier_partial_applies_moves_the_stamp_on_the_next_apply(
    old: Path,
) -> None:
    """A log written before the fix holds the settled items in partial events (from == to);
    one more apply must advance the stamp instead of finding nothing to do forever."""
    frm = "0.1.3"
    EventLog(old, "upgrader").append(
        "upgrade.applied", "upgrade", {"from": frm, "to": frm, "categories": [], "items": []}
    )
    go(
        old, "hooks,instructions,mcp,repairs"
    )  # nothing else is left to apply; config holds the stamp
    before = plan(old)
    assert before["project_version"] == frm
    ids_ = [i["id"] for c in ("config", "features") for i in before["categories"][c]]
    EventLog(old, "upgrader").append(
        "upgrade.applied",
        "upgrade",
        {"from": frm, "to": frm, "categories": ["config"], "backup": "", "items": ids_},
    )
    mid = plan(old)
    assert not mid["categories"]["config"] and mid["project_version"] == frm
    n = len(upgrades(old))

    again = go(old, "all")

    assert again["noop"] or all(r["status"] == "skipped" for r in again["results"])
    assert len(upgrades(old)) == n + 1 and upgrades(old)[-1]["items"] == []
    assert go(old, "all")["noop"] and len(upgrades(old)) == n + 1

    events = [e for e in EventLog(old, "upgrader").read_all() if e.kind == "upgrade.applied"]
    assert not [e for e in events if e.data.get("status") == "failed"]  # a success: no failure note
    assert plan(old)["project_version"] == plan(old)["running"]
    moves = [u for u in upgrades(old) if u["to"] == plan(old)["running"]]
    assert len(moves) == 1  # one stamp-moving record, not two


def test_items_settled_by_a_partial_apply_stay_settled_when_a_newer_ddflow_is_installed(
    old: Path,
) -> None:
    go(old, "all")  # acknowledges what it can, one item is refused
    log, cfg, st = _load(old, "upgrader")

    newer = UP.build(old, log, cfg, st, running="9.9.9")

    assert not [i for i in newer["categories"]["config"] if i["action"] != UP.OPERATOR]


def test_a_value_the_log_redacted_still_counts_as_settled(old: Path) -> None:
    """B81cf6e7dfd: the log records the `~/.claude/projects` default as `[REDACTED:path]`, so the
    settled value never equalled the manifest's and the item came back in every plan. Settled
    items now carry the digest of the value; two paths the log masks alike stay different."""
    from ddflow.services.upgrade_plan import DIGEST, _unsettled

    go(old, "all")
    done = [c for u in upgrades(old) for c in u.get("config_changes") or []]
    assert done and all(c.get("digest") for c in done), done
    dirs = [c for c in done if c["key"] == "approvals.transcript_dirs"]
    assert dirs and "REDACTED" in str(dirs[0]["new"]), dirs  # the log did mask it

    new = ["~/.claude/projects"]
    item = {"id": "knob_added:a.b", "new": new}
    seen = {"knob_added:a.b": [(DIGEST, dirs[0]["digest"])]}
    assert _unsettled([item], seen) == []
    # a changed default is news again even where the log masks both alike
    assert _unsettled([{**item, "new": ["/home/x/projects"]}], seen) != []
    assert _unsettled([{**item, "new": []}], seen) != []
    # a pre-digest record settles nothing (the item is re-acknowledged once, then carries a digest)
    assert _unsettled([item], {"knob_added:a.b": [["[REDACTED:path]"]]}) != []


# -- the lines the mutation sample found surviving (U4, 2026-10-10) --------------------


def test_a_removed_knob_is_swept_from_BOTH_config_files(tmp_path: Path) -> None:
    """A knob that no longer exists can be set in the committed file OR the local one, so
    removing it must reach both.

    The mutant edits only the committed file, and the setting the upgrade was meant to drop
    survives in the local one -- invisibly, because the plan says it was applied.
    """
    from ddflow.services.configwrite import config_file

    cfg = Config()
    removed = {"change": "knob_removed", "key": "log.reuse_parsed"}

    assert UA._config_files(tmp_path, cfg, removed) == [
        config_file(tmp_path),
        config_file(tmp_path, local=True),
    ]


def test_an_ordinary_change_edits_the_file_the_value_came_from(tmp_path: Path) -> None:
    """...and nothing else: a changed value goes to the layer that set it, never to both."""
    from ddflow.services.configwrite import config_file

    cfg = Config()

    assert UA._config_files(tmp_path, cfg, {"change": "knob_changed", "key": "k"}) == [
        config_file(tmp_path)
    ]

    cfg.sources["k"] = "local"
    assert UA._config_files(tmp_path, cfg, {"change": "knob_changed", "key": "k"}) == [
        config_file(tmp_path, local=True)
    ]


def test_the_refusal_names_the_key_and_the_exact_command_to_run() -> None:
    """A refusal is what the operator acts on, so it must name the confirm key and the
    command -- not just say no (the mutant drops the sentence and leaves a bare command)."""
    item: dict[str, Any] = {
        "id": "config:docs.severity",
        "key": "docs.severity",
        "category": "config",
        "action": "",
    }

    text = UA._refusal(item, "ask")

    assert "needs the operator's confirmation" in text
    assert "--confirm docs.severity" in text and "ddflow upgrade --apply" in text

    item["action"] = UP.AGENT
    assert "[upgrade].config_changes = ask" in UA._refusal(item, "ask")


def test_a_fix_that_installs_NOTHING_is_failed_not_applied(tmp_path: Path) -> None:
    """The claude-hooks fix names a file no hook of this tree writes: nothing is installed.

    Reporting APPLIED (the mutant) tells the operator a hook exists when it does not, and
    the upgrade moves on without it.
    """
    missing = "definitely/not/a/hook-file"
    item: dict[str, Any] = {
        "id": "hooks:claude",
        "fix": "ddflow hooks install --claude",
        "paths": [missing],
    }

    status, msg = UA._apply_hook(tmp_path, item)

    assert status == UA.FAILED, msg
    assert "no ddflow hook found" in msg and missing in msg
