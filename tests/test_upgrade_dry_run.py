"""B-upgrade.12-dry-run: the dry-run upgrade on a throwaway clone (D-dry-run-from-running-version).

A small fixture project (a real 0.1.3 release's) is rehearsed end to end: this source tree is the
new ddflow, the old one is this code declaring 0.1.3 (a simulation, which the report says). The
rehearsal runs in a scratch directory with no network and must leave the project alone.
"""

from __future__ import annotations

import json
import os
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
    return DR.Options(source=root, work=kw.pop("work", tmp_path / "scratch"), **kw)


@pytest.fixture(scope="module")
def clean(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, DR.Report, DR.Live]:
    """One full rehearsal of a project that needs no confirmation, with the live project as it was before."""
    tmp = tmp_path_factory.mktemp("dry")
    root = project(tmp, confirm_free=True)
    before = DR.snapshot_live(root)
    report = DR.run_all(options(tmp, root, diag_dir=tmp / "diag"))
    return root, report, before


def problems(before: DR.Live, after: DR.Live, mine: DR.Footprint | None = None) -> list[str]:
    return [*DR.live_leaks(before, after, mine), *DR.live_moved(before, after, mine)]


def rows(report: DR.Report) -> dict[int, DR.Check]:
    return {c.step: c for c in report.checks}


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


# -- B-dryrun-flake: a failure explains itself, and concurrent writers in the live project are
# -- not mistaken for the rehearsal's doing -------------------------------------------------

STEPS = (
    "step_read",
    "step_plan",
    "step_apply",
    "step_old_reader",
    "step_connect",
    "step_agents",
    "step_shards",
    "step_upcaster",
)


def no_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every step after the clone does nothing."""
    for name in STEPS:
        monkeypatch.setattr(DR.Rehearsal, name, lambda self: None)


def only_clone(monkeypatch: pytest.MonkeyPatch, during) -> list[int]:
    """Run nothing but a stand-in for step 1 that calls ``during(rehearsal)``: a concurrent
    writer in the live project, active while the rehearsal runs. Returns the call counter."""
    no_steps(monkeypatch)
    calls: list[int] = []

    def clone(self) -> bool:
        calls.append(1)
        during(self, len(calls))
        return self.add(1, "clone at the installed version's commit", True, "stand-in")

    monkeypatch.setattr(DR.Rehearsal, "step_clone", clone)
    return calls


def failing_step(monkeypatch: pytest.MonkeyPatch, noise: int = 0) -> None:
    """Step 2 runs a command that prints to both streams and exits 3, then fails its row."""
    code = (
        "import sys; print('SOME-STDOUT'); sys.stderr.write('BOOM-FROM-STEP-TWO' + 'x' * "
        f"{noise}); sys.exit(3)"
    )

    def read(self) -> bool:
        done = self.ddflow(DR.Cmd([sys.executable, "-c", code]), self.opt.work, "arg")
        return self.add(2, "read with the new code", False, f"exit {done.returncode}")

    no_steps(monkeypatch)
    monkeypatch.setattr(DR.Rehearsal, "step_read", read)


def test_a_failed_dry_run_persists_its_table_and_each_steps_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    failing_step(monkeypatch, noise=500_000)
    diag = tmp_path / "diag"
    report = DR.run_all(options(tmp_path, root, diag_dir=diag))
    assert report.exit_code == 1
    [path] = sorted(diag.glob("*.json"))
    assert str(path) in report.table()  # the failure line says where to look
    data = json.loads(path.read_text("utf-8"))
    assert [c["step"] for c in data["checks"] if c["status"] == "fail"] == [2]
    ran = [c for c in data["commands"] if "BOOM-FROM-STEP-TWO" in c["stderr"]]
    assert ran and ran[0]["returncode"] == 3 and "SOME-STDOUT" in ran[0]["stdout"]
    assert path.stat().st_size < 100_000  # bounded: half a megabyte of stderr is cut


def test_the_diagnostics_go_to_the_local_directory_unless_told_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    failing_step(monkeypatch)
    monkeypatch.delenv(DR.DIAG_ENV, raising=False)
    DR.run_all(options(tmp_path, root))
    assert list((root / ".ddflow" / "local" / "dryrun").glob("*.json"))
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv(DR.DIAG_ENV, str(elsewhere))
    work = tmp_path / "scratch2"
    DR.run_all(DR.Options(source=root, work=work, old=DR.simulated_old("0.1.3")))
    assert list(elsewhere.glob("*.json"))


def test_a_passing_dry_run_writes_no_diagnostics(clean) -> None:
    root, report, _before = clean
    assert report.exit_code == 0
    assert not (root.parent / "diag").exists()
    assert not (root / ".ddflow" / "local" / "dryrun").exists()


def test_a_rewritten_live_shard_is_a_named_unavailable_result_after_one_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    shard = next((root / ".ddflow" / "events").glob("*.jsonl"))

    def rewrite(_self, n: int) -> None:  # another agent compacts the live log, both times
        shard.write_text(f"rewritten {n}\n", "utf-8")

    calls = only_clone(monkeypatch, rewrite)
    report = DR.run_all(options(tmp_path, root))
    last = rows(report)[10]
    assert last.status == DR.UNAVAILABLE, report.table()
    assert DR.LIVE_CHANGED in last.name + last.detail
    assert report.exit_code == 2 and len(calls) == 2


def test_a_live_log_that_changed_once_is_rehearsed_again_and_the_second_run_decides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    shard = next((root / ".ddflow" / "events").glob("*.jsonl"))

    def rewrite_once(self, n: int) -> None:
        self.git(root, "rev-parse", "HEAD")  # a command, so the transcript has one per run
        if n == 1:
            shard.write_text("rewritten\n", "utf-8")

    calls = only_clone(monkeypatch, rewrite_once)
    report = DR.run_all(options(tmp_path, root))
    assert len(calls) == 2
    assert rows(report)[10].status == DR.PASS, report.table()
    assert DR.LIVE_CHANGED in json.dumps(report.facts)  # the first run is not forgotten
    # ...and neither is its evidence: both runs' tables and commands are in the file
    data = json.loads(Path(report.diagnostics).read_text("utf-8"))
    assert len(data["runs"]) == 2 and len({c["run"] for c in data["commands"]}) == 2
    assert data["runs"][0]["checks"][-1]["status"] == DR.UNAVAILABLE


def test_another_agents_snapshot_tag_and_backup_are_not_the_rehearsals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)

    def other_agent(_self, _n: int) -> None:
        git(
            root,
            "tag",
            "-a",
            "ddflow-upgrade-snapshot/20991231T000000000000Z-0.2.1-to-0.9",
            "-m",
            "x",
        )
        (root / ".ddflow" / "backups" / "20991231T000000000000Z-0.2.1-to-0.9").mkdir(parents=True)

    only_clone(monkeypatch, other_agent)
    report = DR.run_all(options(tmp_path, root))
    assert rows(report)[10].status == DR.PASS, report.table()
    assert report.exit_code == 0


def test_the_rehearsals_own_snapshot_tag_in_the_live_project_is_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)

    def leaked(self, _n: int) -> None:
        env = {**os.environ, "GIT_COMMITTER_NAME": f"dry-run-{self.run_id}"}
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@t.invalid", "tag", "-a",
             "ddflow-upgrade-snapshot/20991231T000000000000Z-0.2.1-to-0.9", "-m", "x"],
            check=True, capture_output=True, env=env,
        )  # fmt: skip

    only_clone(monkeypatch, leaked)
    report = DR.run_all(options(tmp_path, root))
    assert rows(report)[10].status == DR.FAIL
    assert "snapshot tag" in rows(report)[10].detail


def test_a_backup_for_the_rehearsals_versions_made_meanwhile_is_never_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)

    def maybe_leaked(self, _n: int) -> None:
        stamp = DR.clock.compact_at()
        # the rehearsal's own copy, in a clone, and a copy for the same versions in the live
        # project: a leak, or another agent upgrading the same versions meanwhile
        (self.pristine / ".ddflow" / "backups" / f"{stamp}-0.2.1-to-0.9").mkdir(
            parents=True, exist_ok=True
        )
        (root / ".ddflow" / "backups" / f"{stamp}-0.2.1-to-0.9").mkdir(parents=True, exist_ok=True)

    calls = only_clone(monkeypatch, maybe_leaked)
    report = DR.run_all(options(tmp_path, root))
    assert rows(report)[10].status == DR.UNAVAILABLE and len(calls) == 2
    assert "backup" in rows(report)[10].detail and report.exit_code == 2


def test_bounded_keeps_the_ends_and_never_more_than_it_was_given() -> None:
    assert DR.bounded("abc", 10) == "abc"
    assert len(DR.bounded("x" * 1000, 100)) < 200
    cut = DR.bounded("abcdefghij" * 20, 10)
    assert cut.startswith("abcde") and cut.endswith("fghij") and "characters cut" in cut
    assert DR.bounded("abcdef", 1) == "a" and DR.bounded("abcdef", 0) == ""


def test_a_shard_read_while_a_writer_appends_is_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    shard = next((root / ".ddflow" / "events").glob("*.jsonl"))
    real, seen = Path.read_bytes, []

    def flaky(self: Path) -> bytes:
        data = real(self)
        if self == shard and not seen:  # a writer lands right after our first read
            seen.append(1)
            self.write_bytes(data + b'{"late": 1}\n')
        return data

    monkeypatch.setattr(Path, "read_bytes", flaky)
    assert DR.shard_bytes(root)[shard.name].endswith(b'{"late": 1}\n')


def test_a_shard_that_vanishes_between_the_listing_and_the_read_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    shard = next((root / ".ddflow" / "events").glob("*.jsonl"))
    real = Path.read_bytes

    def gone(self: Path) -> bytes:
        if self == shard:
            raise FileNotFoundError(str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", gone)
    assert shard.name not in DR.snapshot_live(root).shards


def test_a_clone_that_fails_once_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    real, failed = DR.Rehearsal.git, []

    def flaky(self, repo, *args):
        if "clone" in args and not failed:  # a gc or a ref update in the live repo, once
            failed.append(1)
            return DR.G.run(
                tmp_path, "clone", "--quiet", str(tmp_path / "nowhere"), str(tmp_path / "x")
            )
        return real(self, repo, *args)

    monkeypatch.setattr(DR.Rehearsal, "git", flaky)
    assert rehearsal.step_clone() is True and failed


def test_a_rehearsal_that_could_not_run_explains_itself_too_but_a_missing_old_ddflow_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    diag = tmp_path / "diag"
    # no old ddflow: steps 5 and 8 are unavailable by design, which needs no transcript
    quiet = DR.run_all(options(tmp_path, root, old=None, diag_dir=diag))
    assert quiet.exit_code == 2 and not diag.exists() and quiet.diagnostics == ""
    # a clone that fails both times (the candidate cause) leaves the git stderr behind
    monkeypatch.setattr(DR, "CLONE_RETRY_S", 0)
    real = DR.Rehearsal.git

    def broken(self, repo, *args):
        if "clone" in args:
            return DR.G.run(
                tmp_path, "clone", "--quiet", str(tmp_path / "nowhere"), str(tmp_path / "x")
            )
        return real(self, repo, *args)

    monkeypatch.setattr(DR.Rehearsal, "git", broken)
    report = DR.run_all(options(tmp_path, root, work=tmp_path / "scratch2", diag_dir=diag))
    assert report.exit_code == 2 and report.diagnostics
    assert report.diagnostics in report.markdown()
    data = json.loads(Path(report.diagnostics).read_text("utf-8"))
    assert data["facts"]["could_not_run"] and "git clone failed twice" in data["table"]


def test_a_build_of_the_old_ddflow_that_fails_leaves_the_uv_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    no_steps(monkeypatch)

    def no_index(argv, **_kw):
        return DR.P.Captured(1, "", "error: no package index (UV-STDERR)")

    monkeypatch.setattr(DR.P, "capture", no_index)
    report = DR.run_all(options(tmp_path, root, old=None, build_old=True, diag_dir=tmp_path / "d"))
    assert report.exit_code == 2 and report.diagnostics
    assert "UV-STDERR" in Path(report.diagnostics).read_text("utf-8")


def test_a_pip_install_that_fails_leaves_the_transcript_and_so_does_an_unwritable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    no_steps(monkeypatch)

    def venv_then_pip_fails(argv, **_kw):
        if argv[:2] == ["uv", "venv"]:
            return DR.P.Captured(0, "", "")
        return DR.P.Captured(1, "", "error: could not install (PIP-STDERR)")

    monkeypatch.setattr(DR.P, "capture", venv_then_pip_fails)
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory", "utf-8")
    report = DR.run_all(options(tmp_path, root, old=None, build_old=True, diag_dir=blocked / "d"))
    try:
        assert report.exit_code == 2 and report.diagnostics
        assert not report.diagnostics.startswith(str(blocked))  # fell back to the temp directory
        data = json.loads(Path(report.diagnostics).read_text("utf-8"))
        assert "PIP-STDERR" in json.dumps(data["commands"])
        assert data["facts"]["could_not_run"] == ["build the old ddflow from the clone"]
    finally:
        shutil.rmtree(Path(report.diagnostics).parent, ignore_errors=True)


def test_the_mcp_sessions_get_time_to_check_and_their_replies_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-dryrun-flake, found by the diagnostics: the server's start check is bounded by
    [upgrade].start_timeout_s (10 s), the plan of a big log takes 7-11 s under load, and past
    the bound the handshake does not propose anything. The rehearsal tests the flow, not the
    speed of the check, so its sessions are given time, and the replies are in the transcript."""
    root = project(tmp_path, confirm_free=True)
    seen: dict[str, str] = {}
    reply = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"instructions": "I-ASK-YOU"}})

    def fake(argv, **kw):
        seen.update(kw["env"])
        return DR.P.Captured(0, reply + "\n", "")

    monkeypatch.setattr(DR.P, "capture", fake)
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    assert rehearsal._serve(root, [])[1] == {"instructions": "I-ASK-YOU"}
    assert float(seen["DDFLOW_UPGRADE_START_TIMEOUT_S"]) >= 60  # the knob's default is 10
    assert "I-ASK-YOU" in rehearsal.commands[-1]["replies"]["1"]


def test_a_start_check_that_ran_out_of_time_is_unavailable_never_a_pass_or_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, confirm_free=True)
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    rehearsal.step_clone()
    timed_out = {
        "instructions": "ddflow 0.2.1 is newer than this project; the upgrade check did not "
        "finish within 10s, so the server started anyway and the upgrade stays pending"
    }
    monkeypatch.setattr(DR.Rehearsal, "_serve", lambda self, repo, msgs, **kw: {1: timed_out})
    rehearsal.step_connect()
    last = rows(rehearsal.report)[6]
    assert last.status == DR.UNAVAILABLE and "time bound" in last.detail
    assert rehearsal.report.facts["could_not_run"]


def test_the_timeout_text_the_rehearsal_looks_for_is_what_the_server_really_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.config import Config
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog
    from ddflow.services import upgrade_start as US

    root = project(tmp_path, confirm_free=True)
    monkeypatch.setenv("DDFLOW_UPGRADE_START_TIMEOUT_S", "0.000001")
    cfg = Config.load(root)
    log = EventLog(root, "t", log_cfg=cfg.log)
    said = US._behind_report(
        root, log, cfg, fold(log.read_all(), strict=False),
        agent="t", surf="mcp", policy="check", running="9.9.9",
    )  # fmt: skip
    assert said["status"] == US.TIMEOUT and DR.START_TIMED_OUT in said["text"]


def test_the_rehearsal_demands_a_note_from_a_start_check_that_ran_out_of_time(
    tmp_path: Path,
) -> None:
    """B-start-timeout-drops-proposal: the handshake of a server given no time is a NOTE that
    the upgrade is pending and the operator is asked; silence or a bare timeout line fails."""
    rehearsal = DR.Rehearsal(options(tmp_path, project(tmp_path, confirm_free=True)))
    note = (
        "ddflow 0.2.1 is newer than this project: an upgrade is PENDING but its plan is not "
        "known yet. Run `ddflow upgrade --plan`. Ask the operator whether to upgrade now"
    )
    assert rehearsal._noted({1: {"instructions": note}}) == []
    proposal = "ddflow 0.2.1 is newer than this project: 72 item(s). Ask the operator whether"
    assert rehearsal._noted({1: {"instructions": proposal}}) == [], "a plan that fit is fine"
    assert rehearsal._noted({1: {"instructions": "did not finish within 10s"}})
    assert rehearsal._noted({1: {"instructions": ""}})
    assert rehearsal._default_path({1: {"instructions": note}}) == []
    assert rehearsal._default_path({1: {"instructions": ""}})


# -- the lines the mutation sample found surviving (U4, 2026-10-10) --------------------


def test_a_shard_that_never_held_still_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shard written while it was read must be REPORTED: the rehearsal cannot treat bytes
    it caught mid-write as the truth, and the mutant silently drops the name."""
    events = tmp_path / ".ddflow" / "events"
    events.mkdir(parents=True)
    for name in ("a.jsonl", "b.jsonl"):
        (events / name).write_bytes(b"{}\n")

    monkeypatch.setattr(DR, "read_stable", lambda p, attempts=3: (b"{}\n", p.name != "a.jsonl"))

    data, unstable = DR.shard_bytes_checked(tmp_path)

    assert sorted(data) == ["a.jsonl", "b.jsonl"], "both shards are still returned"
    assert unstable == ["a.jsonl"], "the one that moved is named"


def _step_read_rehearsal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer) -> DR.Rehearsal:
    """A rehearsal whose `ddflow` calls all answer `answer`, cloning the fixture itself."""
    root = project(tmp_path, confirm_free=True)
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    rehearsal.clone = lambda which: root  # one clone is enough: the fixture IS the project
    monkeypatch.setattr(DR.Rehearsal, "ddflow", lambda *a, **k: answer)
    return rehearsal


def test_step_read_names_what_a_read_command_did_wrong(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step's detail is the failures JOINED: which command broke, with its exit and its
    stderr. The mutant leaves the detail empty, so a failed rehearsal explains nothing."""
    boom = DR.P.Captured(returncode=3, stderr="boom\n")
    rehearsal = _step_read_rehearsal(tmp_path, monkeypatch, boom)

    assert rehearsal.step_read() is False

    check = next(c for c in rehearsal.report.checks if c.name == "read with the new code")
    assert "-> 3 boom" in check.detail


def test_step_read_says_what_it_read_when_nothing_broke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """...and the sentence the passing case shows instead (a passed step that says nothing
    reads like a step that did nothing)."""
    fine = DR.P.Captured(returncode=0)
    rehearsal = _step_read_rehearsal(tmp_path, monkeypatch, fine)

    assert rehearsal.step_read() is True

    check = next(c for c in rehearsal.report.checks if c.name == "read with the new code")
    assert "read commands and" in check.detail and "state unchanged" in check.detail


def test_the_gates_step_names_the_exit_and_the_stderr_of_a_failed_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merge that fails inside the rehearsal must say HOW: its exit status and the tail of
    what it printed, or the operator has a red row and no reason.

    The rehearsal itself is stubbed down to a passing apply, because it is the MESSAGE under
    test, not the rehearsal.
    """
    ok = DR.P.Captured(returncode=0)
    failed = DR.P.Captured(returncode=4, stderr="nothing to merge here\n")
    root = project(tmp_path, confirm_free=True)
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    rehearsal.clone = lambda which: root
    monkeypatch.setattr(DR.Rehearsal, "apply_in", lambda *a, **k: ok)

    def answer(self, cmd, repo, *args, **k):
        return failed if args and args[0] == "merge" else ok

    monkeypatch.setattr(DR.Rehearsal, "ddflow", answer)

    rehearsal.step_agents()

    check = next(c for c in rehearsal.report.checks if c.name == "two agents, one task")
    assert "merge exited 4" in check.detail
    assert "nothing to merge here" in check.detail


def test_the_apply_step_describes_what_held_in_a_clean_rehearsal(clean) -> None:
    """From a real, WHOLE rehearsal (the module fixture): step 4's detail lists what held --
    the records unchanged, the hand edit kept, the second apply a no-op, the restore exact.

    The mutant leaves the detail empty on a passing step, which reads exactly like a step
    that did nothing at all.
    """
    _root, report, _before = clean

    check = next(c for c in report.checks if c.name == "apply with a snapshot, again, restore")
    assert check.status == DR.PASS, check.detail
    assert "users' records unchanged" in check.detail
    assert "second apply a no-op" in check.detail and "restore exact" in check.detail
