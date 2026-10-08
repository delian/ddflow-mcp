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

from ddflow.api import setup as A
from ddflow.api._base import _load
from ddflow.services import backups as BK
from ddflow.services import upgrade_apply as UA

OLD = Path(__file__).parent / "fixtures" / "releases" / "0.1.3" / "project"
DRIVER = "docs/ddflow/drivers/implement-phase.md"


def git(repo: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *argv], capture_output=True, text=True, check=True
    ).stdout.strip()


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
