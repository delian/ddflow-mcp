"""B-upgrade.12-dry-run: the log, the live-project check, the release gate and the script.

Split from tests/test_upgrade_dry_run.py (rule r-small-files); helpers live in upgrade_dry_run_support.py.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from upgrade_dry_run_support import (
    ROOT,
    SCRIPT,
    clean_fixture,  # noqa: F401  (registered as the module fixture `clean`)
    git,
    options,
    problems,
    project,
)

from ddflow.services import upgrade_dryrun as DR


def test_merged_shards_with_a_torn_line_fold_as_the_log_reads_them(tmp_path: Path) -> None:
    """A real log carries lines two events were glued onto (a torn append); the log recovers
    both, so the check of merged shards must read them the same way (found rehearsing ddflow's
    own repository: 4 such lines made the union look short by two events)."""
    root = project(tmp_path, confirm_free=True)
    shard = sorted((root / ".ddflow" / "events").glob("*.jsonl"))[0]
    lines = shard.read_text("utf-8").splitlines()
    shard.write_text("\n".join([*lines[:-2], lines[-2] + lines[-1]]) + "\n", "utf-8")
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "--no-verify", "-m", "a torn append")
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    assert rehearsal.step_clone()
    assert rehearsal.step_shards(), rehearsal.report.table()


def test_the_old_log_folds_through_a_registered_upcaster(tmp_path: Path) -> None:
    from ddflow.core import upcasters as U

    root = project(tmp_path, confirm_free=True)
    kind, problems = DR.upcast_problems(root)
    assert kind and problems == []
    assert U.PAYLOAD_VERSIONS == {} and U.UPCASTERS == {}  # the registries are put back


def test_an_upcaster_the_fold_never_calls_is_a_finding_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check must be able to fail: with the fold's upcasting switched off, no event goes
    through the registered step and the rehearsal says so."""
    from ddflow.core import model as M

    root = project(tmp_path, confirm_free=True)
    monkeypatch.setattr(M, "upcast", lambda ev: ev)
    _kind, problems = DR.upcast_problems(root)
    assert any("went through the upcaster" in p for p in problems), problems


def test_the_registries_come_back_even_when_the_block_raises() -> None:
    from ddflow.core import upcasters as U

    with (
        pytest.raises(RuntimeError),
        DR.registered_upcaster("task.added", lambda k, d: (k, dict(d))),
    ):
        assert U.PAYLOAD_VERSIONS == {"task.added": 2}
        raise RuntimeError("boom")
    assert U.PAYLOAD_VERSIONS == {} and U.UPCASTERS == {}


def test_a_log_with_no_events_cannot_be_rehearsed_with_an_upcaster(tmp_path: Path) -> None:
    (tmp_path / ".ddflow" / "events").mkdir(parents=True)
    assert DR.upcast_problems(tmp_path)[1] == ["the log holds no events"]


def test_the_live_check_tells_our_trace_from_other_agents_work(tmp_path: Path) -> None:
    root = project(tmp_path, confirm_free=True)
    before = DR.snapshot_live(root)
    shard = next((root / ".ddflow" / "events").glob("*.jsonl"))
    # another agent appending to the live log is not the rehearsal's doing
    shard.write_text(shard.read_text("utf-8") + "\n", "utf-8")
    (root / ".ddflow" / "events" / "someone-else.jsonl").write_text("{}\n", "utf-8")
    assert problems(before, DR.snapshot_live(root)) == []
    # ...nor is a snapshot tag or a backup it did not sign or make
    (root / ".ddflow" / "backups" / "x").mkdir(parents=True)
    git(root, "tag", "ddflow-upgrade-snapshot/x")
    mine = DR.Footprint("abc", "0", "9", frozenset({"1-to-2"}))
    assert problems(before, DR.snapshot_live(root), mine) == []
    # ...but a shard of ours is, and a changed byte is named (as the project moving)
    (root / ".ddflow" / "events" / "dry-apply.jsonl").write_text("{}\n", "utf-8")
    shard.write_text("rewritten", "utf-8")
    text = "; ".join(problems(before, DR.snapshot_live(root), mine))
    assert "changed under the run" in text and "dry-apply.jsonl" in text
    assert DR.live_moved(before, DR.snapshot_live(root))
    assert DR.live_leaks(before, DR.snapshot_live(root), mine) == [
        "the live log has a shard of the rehearsal: dry-apply.jsonl"
    ]


def test_a_restore_that_loses_a_shard_byte_is_noticed() -> None:
    assert DR.prefixes_kept({"a": b"abc"}, {"a": b"abcd"}) == []
    assert DR.prefixes_kept({"a": b"abc"}, {"a": b"abd"}) == ["a"]
    assert DR.prefixes_kept({"a": b"abc"}, {}) == ["a"]


def test_the_tools_list_judgement() -> None:
    size, bad = DR.tools_problems([{"name": "t", "description": "d"}], 10_000)
    assert bad == [] and size > 0
    _size, bad = DR.tools_problems([{"name": "t", "outputSchema": {}}], 0)
    assert "output schemas" in bad[0]
    _size, bad = DR.tools_problems([{"name": "t"}], 5)
    assert bad == []  # over the report line is a number, not a failure (D-mcp-budget-report)
    assert DR.tools_problems([], 0)[1] == ["tools/list answered nothing"]


def test_a_release_commit_is_its_tag_else_its_release_commit(tmp_path: Path) -> None:
    root = tmp_path / "rel"
    root.mkdir()
    git(root, "init", "--quiet", "-b", "main")
    for name in ("one", "two", "three"):
        (root / name).write_text(name)
        git(root, "add", "-A")
        git(
            root,
            "commit",
            "--quiet",
            "--no-verify",
            "-m",
            f"release 0.1.{ {'one': 7, 'two': 8, 'three': 9}[name] }",
        )
    git(root, "tag", "v0.1.8", "HEAD~1")
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    tagged = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "v0.1.8"], capture_output=True, text=True
    ).stdout.strip()
    assert DR.release_commit(root, "0.1.8") == tagged
    assert DR.release_commit(root, "0.1.9") == head  # never tagged: found by its commit message
    assert DR.release_commit(root, "9.9.9") == ""
    assert DR.previous_release(root, "0.1.9") == tagged
    assert DR.previous_release(root, "0.1.8") == ""


def test_the_script_wants_to_be_told_which_version(tmp_path: Path) -> None:
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert done.returncode == 2 and "name the commit" in done.stderr
    bad = subprocess.run(
        [sys.executable, str(SCRIPT), "--installed-version", "9.9.9"],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 2 and "no tag or release commit" in bad.stderr


def test_the_script_prints_the_table_and_exits_with_the_verdict(tmp_path: Path) -> None:
    root = project(tmp_path, confirm_free=True)
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--source", str(root), "--from-commit", "HEAD", "--json"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    body = json.loads(done.stdout)
    # no old ddflow was named: its rows could not run, and nothing else failed
    assert done.returncode == body["exit"] == 2, done.stdout
    assert {c["step"] for c in body["checks"] if c["status"] == "unavailable"} == {5, 8}


def test_a_release_is_refused_when_the_dry_run_from_the_previous_one_is_red() -> None:
    release = (ROOT / "scripts" / "release.sh").read_text("utf-8")
    assert "scripts/ci/upgrade-dry-run --previous-release" in release
    assert "DDFLOW_SKIP_DRYRUN" in release and "die " in release
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text("utf-8")
    at = workflow.index("upgrade-dry-run --previous-release")
    # the job that runs it is one the publishing job NEEDS: job order in the file means nothing
    heads = [(m.start(), m.group(1)) for m in re.finditer(r"^  ([\w-]+):\s*$", workflow, re.M)]
    job = [name for start, name in heads if start < at][-1]
    pypi_at = next(start for start, name in heads if name == "pypi")
    after = [start for start, _ in heads if start > pypi_at]
    pypi = workflow[pypi_at : after[0] if after else len(workflow)]
    needs = re.search(r"^    needs:\s*\[([^\]]*)\]", pypi, re.M)
    assert needs, "the pypi job no longer says (in a flow list) what it needs"
    assert job in [n.strip() for n in needs.group(1).split(",")], (job, needs.group(1))
