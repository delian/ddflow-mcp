"""B-upgrade.4-apply.3-snapshot: the snapshot backup mode and `upgrade --restore` (D-upgrade-backups).

`[upgrade].backup = "snapshot"` tags HEAD (after committing affected files git did not track
yet) before an apply, so a git revert or `--restore` undoes it; a dirty tree or no git is a
clear refusal. `--restore` puts back a local backup or a snapshot, saving what it replaces.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import run_cli
from helpers import git

from ddflow.api import setup as A
from ddflow.api._base import _load
from ddflow.services import backups as BK
from ddflow.services import upgrade_apply as UA

OLD = Path(__file__).parent / "fixtures" / "releases" / "0.1.3" / "project"
DRIVER = "docs/ddflow/drivers/implement-phase.md"


@pytest.fixture
def old(tmp_path: Path) -> Path:
    r = tmp_path / "old"
    shutil.copytree(OLD, r)
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    for k, v in (("user.email", "t@e.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    git(r, "add", "-A")
    git(r, "commit", "-qm", "old")
    return r


def go(repo: Path, categories: Any = None, **kw: Any) -> dict[str, Any]:
    log, cfg, st = _load(repo, "upgrader")
    return UA.apply(repo, log, cfg, st, categories=categories, agent="upgrader", **kw)


def tags(repo: Path) -> list[str]:
    return [t for t in git(repo, "tag", "--list").splitlines() if t.startswith(BK.SNAPSHOT_PREFIX)]


def test_a_snapshot_tags_head_before_the_apply_and_names_it(old: Path) -> None:
    head = git(old, "rev-parse", "HEAD")
    original = (old / DRIVER).read_bytes()

    out = go(old, "instructions", backup="snapshot")

    assert out["exit"] == 0, out["text"]
    (tag,) = tags(old)
    assert out["backup"].startswith(tag) and tag.startswith(BK.SNAPSHOT_PREFIX)
    assert git(old, "rev-parse", f"{tag}^{{commit}}") == head, (
        "nothing to commit: HEAD is the snapshot"
    )
    shown = subprocess.run(
        ["git", "-C", str(old), "show", f"{tag}:{DRIVER}"], capture_output=True, check=True
    ).stdout
    assert shown == original, "the tag holds the file as it was BEFORE the apply"
    assert (old / DRIVER).read_bytes() != original


def test_a_dirty_tree_is_refused_clearly_and_nothing_changes(old: Path) -> None:
    (old / "AGENTS.md").write_text((old / "AGENTS.md").read_text() + "\nmy uncommitted note\n")
    before = (old / DRIVER).read_bytes()

    out = go(old, "instructions", backup="snapshot")

    assert out["exit"] == 3 and "clean working tree" in out["text"]
    assert "[local]" not in out["text"] and "use [upgrade].backup = local" in out["text"]
    assert (old / DRIVER).read_bytes() == before and tags(old) == []


def test_without_git_a_snapshot_is_refused_clearly(old: Path) -> None:
    shutil.rmtree(old / ".git")

    out = go(old, "instructions", backup="snapshot")

    assert out["exit"] == 3 and "needs a git repository" in out["text"]


def test_a_file_git_does_not_track_yet_is_committed_into_the_snapshot(old: Path) -> None:
    mcp = old / ".cursor" / "mcp.json"  # the fixture has none: git does not track it yet
    mcp.parent.mkdir()
    mcp.write_text(
        json.dumps({"mcpServers": {"ddflow": {"command": "/nonexistent/venv/bin/python"}}})
    )
    stale = mcp.read_bytes()

    out = go(old, "mcp", backup="snapshot")

    assert out["exit"] == 0, out["text"]
    (tag,) = tags(old)
    assert git(old, "log", "-1", "--format=%s", tag) == "ddflow: snapshot before upgrade"
    assert git(old, "show", f"{tag}:.cursor/mcp.json").encode() == stale
    assert mcp.read_bytes() != stale


def test_restoring_a_snapshot_puts_the_files_back_and_removes_what_the_apply_created(
    old: Path,
) -> None:
    before = (old / DRIVER).read_bytes()
    go(old, "instructions,hooks", backup="snapshot")
    assert (old / DRIVER).read_bytes() != before and (old / ".claude" / "settings.json").exists()

    done = BK.restore(old, "latest")

    assert done["kind"] == "snapshot"
    assert (old / DRIVER).read_bytes() == before
    assert not (old / ".claude" / "settings.json").exists()
    assert Path(done["saved"]).is_dir(), "what was there is saved first, so a restore can be undone"
    assert DRIVER in done["restored"] and ".claude/settings.json" in done["removed"]


def test_restoring_a_local_backup_by_name_and_as_latest(old: Path) -> None:
    before = (old / DRIVER).read_bytes()
    out = go(old, "instructions,hooks")
    name = Path(out["backup"]).name

    done = BK.restore(old, name)

    assert done["kind"] == "backup" and (old / DRIVER).read_bytes() == before
    assert not (old / ".claude" / "settings.json").exists()
    again = BK.restore(old, "latest")
    assert again["name"] != name, "latest is the safety copy the first restore made"


def test_a_restore_keeps_the_mode_of_a_file(old: Path, tmp_path: Path) -> None:
    script = old / "tools" / "run.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    dest = BK.make_backup(old, [script], "a", "b")
    script.write_text("changed\n")
    script.chmod(0o644)

    BK.restore(old, dest.name)

    assert script.read_text() == "#!/bin/sh\n" and script.stat().st_mode & 0o111


def test_an_unknown_name_lists_what_there_is_and_nothing_to_restore_is_said(old: Path) -> None:
    with pytest.raises(LookupError, match="no upgrade backup or snapshot to restore"):
        BK.restore(old, "latest")
    go(old, "hooks")
    with pytest.raises(LookupError, match="no upgrade backup or snapshot named 'nope'; known: "):
        BK.restore(old, "nope")


def test_the_api_restore_reports_what_it_did_and_stands_alone(old: Path) -> None:
    go(old, "hooks")

    out = A.upgrade(old, restore="latest", agent="upgrader")

    assert out.exit == 0 and out.data["restored"]["kind"] == "backup"
    assert ".claude/settings.json" in out.data["restored"]["removed"]
    assert "restored from backup" in out.data["text"] and "saved in" in out.data["text"]
    both = A.upgrade(old, restore="latest", apply="hooks", agent="upgrader")
    assert both.exit == 3 and "stands alone" in both.reason
    assert A.upgrade(old, restore="nope", agent="upgrader").exit == 1


def test_the_snapshot_choice_is_a_knob_value_and_a_bad_one_is_still_local(old: Path) -> None:
    assert run_cli(old, "config", "--set", "upgrade.backup", "snapshot")[0] == 0
    # the edit itself is an uncommitted change, which a snapshot refuses: commit it first
    out = A.upgrade(old, apply="hooks", agent="upgrader")
    assert out.exit == 3 and "clean working tree" in out.data["applied"]["results"][0]["detail"]
    git(old, "add", "-A")
    git(old, "commit", "-qm", "set the knob")
    _log, cfg, _st = _load(old, "upgrader")
    assert cfg.upgrade.backup == "snapshot"
    out = A.upgrade(old, apply="hooks", agent="upgrader")
    assert out.exit == 0 and out.data["applied"]["backup"].startswith(BK.SNAPSHOT_PREFIX)


def test_a_snapshot_of_a_repository_with_no_commit_is_refused(tmp_path: Path) -> None:
    r = tmp_path / "fresh"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    # nothing exists to commit and HEAD does not exist either
    with pytest.raises(BK.SnapshotRefused, match=r"needs a commit"):
        BK.make_snapshot(r, [r / "not-there.txt"], "a", "b")


def test_a_restore_that_git_refuses_is_exit_3_not_a_failure(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(repo: Path, name: str) -> dict[str, Any]:
        raise BK.SnapshotRefused("git could not restore the snapshot: no")

    monkeypatch.setattr(BK, "restore", refuse)

    out = A.upgrade(old, restore="latest", agent="upgrader")

    assert out.exit == 3 and "git could not restore" in out.reason


def test_a_disk_failure_while_saving_is_a_failure_not_a_snapshot_refusal(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_disk(*a: Any, **k: Any) -> BK.Snapshot:
        raise OSError("read-only file system")

    monkeypatch.setattr(UA, "make_snapshot", no_disk)

    out = go(old, "hooks", backup="snapshot")

    assert out["exit"] == 1 and "[failed]" in out["text"] and "[refused]" not in out["text"]


def test_what_git_cannot_hold_is_a_sidecar_that_the_tag_restore_puts_back(old: Path) -> None:
    hook = old / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexec /nonexistent/ddflow hooks check-commit\n# ddflow-managed\n")
    hook.chmod(0o755)
    snap = BK.make_snapshot(old, [hook, old / DRIVER], "a", "b")

    assert snap.local and snap.held == (DRIVER,)
    assert BK.local_backups(old) == [], "a sidecar is not a restore point of its own"
    assert BK.newest(old) == snap.tag
    hook.write_text("changed\n")
    (old / DRIVER).write_text("changed\n")

    done = BK.restore(old, snap.tag)

    assert hook.read_text().startswith("#!/bin/sh") and hook.stat().st_mode & 0o111
    assert (old / DRIVER).read_text() != "changed\n"
    assert set(done["restored"]) >= {DRIVER, ".git/hooks/pre-commit"}


def test_backup_keep_also_bounds_snapshot_runs_and_restores(old: Path) -> None:
    assert run_cli(old, "config", "--set", "upgrade.backup_keep", "1")[0] == 0
    git(old, "add", "-A")
    git(old, "commit", "-qm", "knob")
    for n in range(3):
        BK.make_backup(old, [old / DRIVER], "a", str(n))
    assert len(BK.local_backups(old)) == 3

    A.upgrade(old, restore="latest", agent="upgrader")

    assert len(BK.local_backups(old)) == 1


def test_the_backup_modes_the_service_accepts_are_the_knob_choices() -> None:
    from ddflow.config import KNOB_CHOICES

    assert UA.BACKUP_MODES == KNOB_CHOICES["upgrade.backup"]


def test_prune_never_evicts_the_sidecar_of_a_tag_that_still_exists(old: Path) -> None:
    hook = old / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n# ddflow-managed\n")
    snap = BK.make_snapshot(old, [hook], "a", "b")
    for n in range(3):
        BK.make_backup(old, [old / DRIVER], "a", f"later{n}")

    removed = BK.prune(old, 1)

    assert Path(snap.local).is_dir(), "the tag still exists, so does its sidecar"
    assert Path(snap.local).name not in removed and len(removed) == 2
    git(old, "tag", "-d", snap.tag)
    assert Path(snap.local).name in BK.prune(old, 1), "with the tag gone the sidecar goes too"


def test_a_backup_with_a_damaged_manifest_is_listed_and_restoring_it_says_why_not(
    old: Path,
) -> None:
    dest = BK.make_backup(old, [old / DRIVER], "a", "b")
    (dest / BK.MANIFEST).write_text("{not json")

    assert BK.local_backups(old) == [dest.name]
    with pytest.raises(LookupError, match="cannot be read"):
        BK.restore(old, "latest")
    assert A.upgrade(old, restore="latest", agent="upgrader").exit == 1


def test_a_failed_commit_leaves_no_staged_files_and_no_sidecar(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = old / "fresh.txt"  # affected, untracked: the snapshot has to commit it
    fresh.write_text("x\n")
    hook = old / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n# ddflow-managed\n")
    git(old, "config", "commit.gpgsign", "false")
    (old / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexit 1\n")  # the commit fails
    hook.chmod(0o755)
    head = git(old, "rev-parse", "HEAD")

    with pytest.raises(BK.SnapshotRefused, match="could not commit"):
        BK.make_snapshot(old, [fresh, hook], "a", "b")

    assert git(old, "status", "--porcelain", "--untracked-files=no") == "", "nothing stays staged"
    assert git(old, "rev-parse", "HEAD") == head and tags(old) == []
    backups = old / ".ddflow" / "backups"
    assert not backups.exists() or not [p for p in backups.iterdir() if p.is_dir()], "no sidecar"


def test_a_tag_that_cannot_be_made_undoes_the_commit_it_made(old: Path) -> None:
    fresh = old / "fresh.txt"
    fresh.write_text("x\n")
    head = git(old, "rev-parse", "HEAD")
    tag = f"{BK.SNAPSHOT_PREFIX}{BK.backup_name('a', 'b')}"
    git(old, "tag", tag)  # the name is taken
    import ddflow.services.backups as mod

    original = mod.backup_name
    mod.backup_name = lambda frm, to: tag.removeprefix(BK.SNAPSHOT_PREFIX)
    try:
        with pytest.raises(BK.SnapshotRefused, match="could not tag"):
            BK.make_snapshot(old, [fresh], "a", "b")
    finally:
        mod.backup_name = original

    assert git(old, "rev-parse", "HEAD") == head, "our commit is undone"
    assert (
        fresh.read_text() == "x\n"
        and git(old, "status", "--porcelain", "--untracked-files=no") == ""
    )


def test_a_dangling_link_is_not_mistaken_for_a_file_that_did_not_exist(old: Path) -> None:
    import os

    link = old / "link"
    os.symlink("/nonexistent/target", link)

    snap = BK.make_snapshot(old, [link], "a", "b")

    assert "link" not in snap.created


def test_a_snapshot_apply_prunes_the_local_backups_too(old: Path) -> None:
    assert run_cli(old, "config", "--set", "upgrade.backup_keep", "1")[0] == 0
    git(old, "add", "-A")
    git(old, "commit", "-qm", "knob")
    for n in range(3):
        BK.make_backup(old, [old / DRIVER], "a", f"older{n}")

    out = go(old, "hooks", backup="snapshot")

    assert out["exit"] == 0, out["text"]
    assert len(BK.local_backups(old)) == 1, "backup_keep applies in snapshot mode as well"


def test_a_sidecar_whose_manifest_is_unreadable_is_still_a_sidecar(old: Path) -> None:
    hook = old / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n# ddflow-managed\n")
    snap = BK.make_snapshot(old, [hook], "a", "b")
    (Path(snap.local) / BK.MANIFEST).write_text("{broken")
    for n in range(3):
        BK.make_backup(old, [old / DRIVER], "a", f"later{n}")

    removed = BK.prune(old, 1)

    assert Path(snap.local).is_dir() and Path(snap.local).name not in removed
    assert snap.local.endswith(BK.SIDECAR_SUFFIX) and BK.local_backups(old) != []


def test_a_symlink_is_held_as_the_link_not_as_its_target(old: Path) -> None:
    import os

    target = old / "target.txt"
    target.write_text("x\n")
    git(old, "add", "-A")
    git(old, "commit", "-qm", "target")
    link = old / "link"
    os.symlink("target.txt", link)
    git(old, "add", "link")
    git(old, "commit", "-qm", "link")

    snap = BK.make_snapshot(old, [link], "a", "b")

    assert snap.held == ("link",) and snap.created == ()


def test_a_damaged_sidecar_refuses_the_whole_restore_before_anything_changes(old: Path) -> None:
    hook = old / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n# ddflow-managed\n")
    snap = BK.make_snapshot(old, [hook, old / DRIVER], "a", "b")
    (old / DRIVER).write_text("changed\n")
    (Path(snap.local) / BK.MANIFEST).write_text("{broken")

    with pytest.raises(LookupError, match="cannot be read"):
        BK.restore(old, snap.tag)

    assert (old / DRIVER).read_text() == "changed\n", "no file was touched"


def test_a_symlink_is_backed_up_and_restored_as_a_link(old: Path) -> None:
    import os

    real = old / "real.txt"
    real.write_text("data\n")
    link = old / "link.txt"
    os.symlink("real.txt", link)
    dest = BK.make_backup(old, [link], "a", "b")
    link.unlink()
    link.write_text("a plain file now\n")

    BK.restore(old, dest.name)

    assert link.is_symlink() and os.readlink(link) == "real.txt"
    assert real.read_text() == "data\n", "the target was never written through"


# -- B-upgrade.4-apply.3b-wire: the CLI flags and the MCP arguments ---------------------------


def test_cli_snapshot_and_restore_round_trip(old: Path) -> None:
    before = (old / DRIVER).read_bytes()
    code, out, err = run_cli(old, "upgrade", "--apply", "instructions", "--snapshot")
    assert code == 0, (out, err)
    (tag,) = tags(old)
    assert tag in out and (old / DRIVER).read_bytes() != before

    code, out, err = run_cli(old, "upgrade", "--restore")
    assert code == 0, (out, err)
    assert out.startswith("restored from snapshot") and (old / DRIVER).read_bytes() == before
    code, out, _ = run_cli(old, "--json", "upgrade", "--restore", tag)
    body = json.loads(out)
    assert code == 0 and body["restored"]["name"] == tag and "applied" not in body


def test_cli_snapshot_needs_apply_and_does_not_contradict_backup(old: Path) -> None:
    code, _out, err = run_cli(old, "upgrade", "--snapshot")
    assert code == 3 and "--apply" in err
    code, _out, err = run_cli(old, "upgrade", "--apply", "hooks", "--snapshot", "--backup", "none")
    assert code == 3 and "disagree" in err
    assert tags(old) == []
    code, _out, err = run_cli(old, "upgrade", "--restore", "--snapshot")
    assert code == 3 and "stands alone" in err
    code, _out, err = run_cli(old, "upgrade", "--restore", "--apply", "hooks")
    assert code == 3 and "stands alone" in err
    for extra in (
        ("--backup", "none"),
        ("--backup", "snapshot"),
        ("--reason", "why"),
        ("--plan",),
        ("--confirm", "K", "--reason", "why"),
    ):
        code, _out, err = run_cli(old, "upgrade", "--restore", *extra)
        assert code == 3 and "stands alone" in err, extra
    assert tags(old) == []


def test_the_mcp_tool_carries_snapshot_and_restore(old: Path) -> None:
    from ddflow.surfaces import tools as M

    spec = M.TOOLS["ddflow_upgrade"]
    assert {"snapshot", "restore", "backup"} <= set(spec["properties"])
    before = (old / DRIVER).read_bytes()
    out = spec["api"](old, {"apply": "instructions", "snapshot": True}, "upgrader")
    assert out.exit == 0 and len(tags(old)) == 1
    assert (old / DRIVER).read_bytes() != before, "the apply changed the file"
    done = spec["api"](old, {"restore": "latest"}, "upgrader")
    assert done.exit == 0 and (old / DRIVER).read_bytes() == before
    payload = spec["payload"]({"restore": "latest"})
    assert "restored" in payload and "applied" not in payload
    assert "applied" in spec["payload"]({"apply": "hooks"})


def test_the_mcp_tool_refuses_what_the_cli_refuses(old: Path) -> None:
    from ddflow.surfaces import tools as M

    api = M.TOOLS["ddflow_upgrade"]["api"]
    for args in (
        {"restore": "latest", "snapshot": True},
        {"restore": "latest", "apply": "hooks"},
        {"restore": "latest", "backup": "none"},
        {"restore": "latest", "reason": "why"},
        {"restore": "latest", "plan": True},
        {"restore": "latest", "confirm": ["K"], "reason": "why"},
        {"snapshot": True},
        {"apply": "hooks", "snapshot": True, "backup": "none"},
    ):
        out = api(old, args, "upgrader")
        assert out.exit == 3, (args, out.exit, out.reason)
    assert tags(old) == []
