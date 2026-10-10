"""B-upgrade.12-dry-run: the dry-run upgrade on a throwaway clone (D-dry-run-from-running-version).

A small fixture project (a real 0.1.3 release's) is rehearsed end to end: this source tree is the
new ddflow, the old one is this code declaring 0.1.3 (a simulation, which the report says). The
rehearsal runs in a scratch directory with no network and must leave the project alone.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.services import upgrade_dryrun as DR

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "releases" / "0.1.3" / "project"
SCRIPT = ROOT / "scripts" / "ci" / "upgrade-dry-run"
BUDGET = int(
    re.search(
        r"^TOOLS_LIST_BUDGET\s*=\s*([\d_]+)",
        (ROOT / "tests" / "test_mcp_tool_budget.py").read_text("utf-8"),
        re.M,
    )
    .group(1)
    .replace("_", "")
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid", *args],
        check=True,
        capture_output=True,
    )


def project(tmp_path: Path, *, confirm_free: bool) -> Path:
    """The 0.1.3 fixture as a git project. ``confirm_free`` drops the one operator-set value
    the new defaults would ask the operator to confirm."""
    root = tmp_path / "proj"
    shutil.copytree(FIXTURE, root)
    if confirm_free:
        cfg = root / ".ddflow" / "config.toml"
        cfg.write_text(cfg.read_text("utf-8").replace("max_parallel = 4\n", ""), "utf-8")
    git(root, "init", "--quiet", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "--no-verify", "-m", "fixture")
    return root


def options(tmp_path: Path, root: Path, **kw) -> DR.Options:
    kw.setdefault("old", DR.simulated_old("0.1.3"))
    kw.setdefault("tools_budget", BUDGET)
    return DR.Options(source=root, work=tmp_path / "scratch", **kw)


@pytest.fixture(scope="module")
def clean(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, DR.Report, DR.Live]:
    """One full rehearsal of a project that needs no confirmation, with the live project as it was before."""
    tmp = tmp_path_factory.mktemp("dry")
    root = project(tmp, confirm_free=True)
    before = DR.snapshot_live(root)
    report = DR.run_all(options(tmp, root))
    return root, report, before


def rows(report: DR.Report) -> dict[int, DR.Check]:
    return {c.step: c for c in report.checks}


def test_a_smooth_upgrade_passes_every_check(clean) -> None:
    _root, report, _before = clean
    assert report.exit_code == 0, report.table()
    assert sorted(rows(report)) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert all(c.status == DR.PASS for c in report.checks), report.table()


def test_the_live_project_is_left_alone(clean) -> None:
    root, report, before = clean
    assert DR.live_problems(before, DR.snapshot_live(root)) == []
    assert rows(report)[9].status == DR.PASS
    # the scratch directory is gone; nothing of the rehearsal is left in the project
    assert not (root / ".ddflow" / "backups").exists()


def test_the_connect_flow_proposes_then_waits_for_consent(clean) -> None:
    _root, report, _before = clean
    detail = rows(report)[6].detail
    assert "proposed on connect, nothing applied until consent, then applied" in detail
    assert str(BUDGET)[:2] in detail or "tools/list" in detail  # the size is reported


def test_the_old_ddflow_is_refused_its_write_with_the_remedy(clean) -> None:
    _root, report, _before = clean
    assert "refused its write with the remedy" in rows(report)[5].detail


def test_the_report_is_a_table_a_markdown_page_and_json(clean) -> None:
    _root, report, _before = clean
    assert "dry run PASS" in report.table()
    page = report.markdown()
    assert "| 4 | apply with a snapshot, again, restore | pass |" in page
    assert json.loads(json.dumps(report.as_data()))["exit"] == 0


def test_a_confirmation_the_operator_would_hit_fails_the_plan_row(tmp_path: Path) -> None:
    root = project(tmp_path, confirm_free=False)
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    rehearsal.step_clone()
    rehearsal.step_plan()
    plan = rows(rehearsal.report)[3]
    assert plan.status == DR.FAIL and "worktree.max_parallel" in plan.detail
    assert rehearsal.confirms == ["worktree.max_parallel"]
    assert "worktree.max_parallel" in rehearsal.report.preview
    # the release check names it without failing
    lenient = DR.Rehearsal(DR.Options(source=root, work=tmp_path / "scratch2", asked_fails=False))
    lenient.step_clone()
    lenient.step_plan()
    assert rows(lenient.report)[3].status == DR.PASS


def test_without_an_old_ddflow_those_rows_are_unavailable_never_a_pass(tmp_path: Path) -> None:
    root = project(tmp_path, confirm_free=True)
    rehearsal = DR.Rehearsal(options(tmp_path, root, old=None))
    rehearsal.step_clone()
    rehearsal.step_old_reader()
    rehearsal.step_shards()
    got = rows(rehearsal.report)
    assert got[5].status == got[8].status == DR.UNAVAILABLE
    assert rehearsal.report.exit_code == 2


def test_a_failing_check_exits_1_and_an_unrunnable_one_exits_2() -> None:
    rep = DR.Report("s", "c", "n", "o")
    rep.checks = [DR.Check(1, "a", DR.PASS), DR.Check(2, "b", DR.UNAVAILABLE)]
    assert rep.exit_code == 2
    rep.checks.append(DR.Check(3, "c", DR.FAIL))
    assert rep.exit_code == 1
    assert DR.Report("s", "c", "n", "o").exit_code == 2  # nothing ran


def test_the_scratch_directory_cannot_be_inside_the_project(tmp_path: Path) -> None:
    root = project(tmp_path, confirm_free=True)
    report = DR.run_all(DR.Options(source=root, work=root / "scratch"))
    clone = rows(report)[1]
    assert clone.status == DR.UNAVAILABLE and "outside the project" in clone.detail
    assert report.exit_code == 2


def test_a_scratch_directory_that_is_not_empty_is_refused_and_kept(tmp_path: Path) -> None:
    root = project(tmp_path, confirm_free=True)
    work = tmp_path / "mine"
    work.mkdir()
    (work / "precious").write_text("keep me")
    report = DR.run_all(DR.Options(source=root, work=work))
    assert rows(report)[1].status == DR.UNAVAILABLE
    assert (work / "precious").read_text() == "keep me"


def test_an_unknown_commit_could_not_run(tmp_path: Path) -> None:
    root = project(tmp_path, confirm_free=True)
    report = DR.run_all(options(tmp_path, root, from_commit="no-such-commit"))
    assert report.exit_code == 2 and "no commit" in rows(report)[1].detail


def test_history_that_already_holds_an_upgrade_is_not_taken_for_a_premature_apply(
    tmp_path: Path,
) -> None:
    """A project upgraded before has `upgrade.applied` events in its log: only NEW ones, written
    during the connect, mean something was applied before the operator consented."""
    from ddflow.core.events import Event

    root = project(tmp_path, confirm_free=True)
    shard = sorted((root / ".ddflow" / "events").glob("*.jsonl"))[0]
    past = Event(
        kind="upgrade.applied",
        subject="upgrade",
        data={"from": "0.0.1", "to": "0.0.2", "categories": [], "items": []},
        agent="someone",
        lamport=10**6,
        ts="2026-01-01T00:00:00.000000Z",
    )
    shard.write_text(shard.read_text("utf-8") + past.to_json() + "\n", "utf-8")
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "--no-verify", "-m", "upgraded before")
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    assert rehearsal.step_clone()
    assert rehearsal.step_connect(), rehearsal.report.table()


def test_the_new_ddflow_is_never_the_clones_own_package(tmp_path: Path) -> None:
    """Rehearsing ddflow's own repository: `python -m ddflow` run inside the clone imports the
    clone's `ddflow` package first (the working directory leads sys.path) unless told not to, and
    the "new" ddflow would be the OLD source."""
    root = project(tmp_path, confirm_free=True)
    shadow = root / "ddflow"
    shadow.mkdir()
    (shadow / "__init__.py").write_text('raise RuntimeError("the clone package was imported")\n')
    (shadow / "__main__.py").write_text("")
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "--no-verify", "-m", "a package called ddflow")
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    assert rehearsal.step_clone()
    assert rehearsal.step_read(), rehearsal.report.table()
    assert rows(rehearsal.report)[2].status == DR.PASS


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


def test_the_live_check_tells_our_trace_from_other_agents_work(tmp_path: Path) -> None:
    root = project(tmp_path, confirm_free=True)
    before = DR.snapshot_live(root)
    shard = next((root / ".ddflow" / "events").glob("*.jsonl"))
    # another agent appending to the live log is not the rehearsal's doing
    shard.write_text(shard.read_text("utf-8") + "\n", "utf-8")
    (root / ".ddflow" / "events" / "someone-else.jsonl").write_text("{}\n", "utf-8")
    assert DR.live_problems(before, DR.snapshot_live(root)) == []
    # ...but a changed byte, a shard of ours, a snapshot tag or a backup is
    (root / ".ddflow" / "events" / "dry-apply.jsonl").write_text("{}\n", "utf-8")
    (root / ".ddflow" / "backups" / "x").mkdir(parents=True)
    git(root, "tag", "ddflow-upgrade-snapshot/x")
    shard.write_text("rewritten", "utf-8")
    text = "; ".join(DR.live_problems(before, DR.snapshot_live(root)))
    assert "changed under the run" in text and "dry-apply.jsonl" in text
    assert "snapshot tag" in text and "a backup appeared" in text


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
    assert "over the 5 budget" in bad[0]
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
    assert "upgrade-dry-run --previous-release" in workflow
    # it sits in the job every publishing job needs, before PyPI
    assert workflow.index("upgrade-dry-run --previous-release") < workflow.index("\n  pypi:")
