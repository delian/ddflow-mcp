"""B-upgrade.12-dry-run: the rehearsal end to end: its rows, its refusals and the clone.

Split from tests/test_upgrade_dry_run.py (rule r-small-files); helpers live in upgrade_dry_run_support.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from upgrade_dry_run_support import (
    BUDGET,
    clean_fixture,  # noqa: F401  (registered as the module fixture `clean`)
    git,
    options,
    problems,
    project,
    rows,
)

from ddflow.services import upgrade_dryrun as DR


def test_a_smooth_upgrade_passes_every_check(clean) -> None:
    _root, report, _before = clean
    assert report.exit_code == 0, report.table()
    assert sorted(rows(report)) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert all(c.status == DR.PASS for c in report.checks), report.table()


def test_the_live_project_is_left_alone(clean) -> None:
    root, report, before = clean
    assert problems(before, DR.snapshot_live(root)) == []
    assert rows(report)[10].status == DR.PASS
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


def test_a_hand_edit_inside_a_stamped_block_is_written_and_found_again(tmp_path: Path) -> None:
    root = project(tmp_path, confirm_free=True)
    (root / "CLAUDE.md").write_text(
        "# mine\n<!-- ddflow:begin rules/work-queue ddflow=0.1.0 fmt=1 sha=abc -->\nbody\n"
        "<!-- ddflow:end rules/work-queue -->\n",
        "utf-8",
    )
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "--no-verify", "-m", "a stamped block")
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    assert rehearsal.step_clone()
    repo = rehearsal.clone("edit")
    assert rehearsal._hand_edit(repo) == "CLAUDE.md"
    text = (repo / "CLAUDE.md").read_text("utf-8").splitlines()
    assert text[2] == DR.HAND_EDIT  # inside the block, right after its begin marker
    assert DR._edit_kept(repo, "CLAUDE.md")
    # an apply that rewrote the file but kept the line beside it did not lose it
    (repo / "CLAUDE.md").write_text("rewritten\n", "utf-8")
    assert not DR._edit_kept(repo, "CLAUDE.md")
    (repo / "CLAUDE.md.local-edits").write_text(DR.HAND_EDIT, "utf-8")
    assert DR._edit_kept(repo, "CLAUDE.md")


def test_a_project_without_a_stamped_block_has_nothing_to_hand_edit(tmp_path: Path) -> None:
    root = project(tmp_path, confirm_free=True)
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    assert rehearsal.step_clone()
    assert rehearsal._hand_edit(rehearsal.clone("edit")) == ""


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
