"""After every completion a minimal progress block: where the whole queue stands (B-hy-progress-line)."""

from __future__ import annotations

import json

from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import progress_line as PL


def _project(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    for t in ("T1", "T2", "T3"):
        assert (
            run_cli(repo, "task", "add", t, "--title", t, "--phase", "P1", "--globs", f"{t}.py")[0]
            == 0
        )
    run_cli(
        repo,
        "bug",
        "found",
        "--summary",
        "b one breaks",
        "--severity",
        "high",
        "--globs",
        "x.py",
        "--new",
    )


def test_complete_reports_tasks_bugs_phases_and_next(repo):
    _project(repo)
    code, out, err = run_cli(repo, "complete", "T1", "--force")
    assert code == 0, err
    lines = out.splitlines()
    progress = next(ln for ln in lines if ln.startswith("Progress:"))
    assert "tasks 1/3 (33%)" in progress
    assert "bugs fixed 0/1 (0%), 1 open (1 high)" in progress
    assert "phases 0/2 (0%)" in progress  # P1 and the bugs phase the fix task went to
    assert "Phase P1: 1/3 (33%)" in lines
    nxt = next(ln for ln in lines if ln.startswith("Next: "))
    assert "T2" in nxt or "fix-" in nxt


def test_the_block_stays_small(repo):
    _project(repo)
    out = run_cli(repo, "complete", "T1", "--force")[1]
    block = out[out.index("Progress:") :]
    assert len(block) < 400 and block.count("\n") <= 3


def test_a_phase_whose_tasks_are_done_is_reported_ready_to_close(repo):
    _project(repo)
    st = fold(EventLog(repo).read_all(), strict=False)
    fix = next(i for i in st.items.values() if i.fixes)
    for t in ("T1", "T2", "T3", fix.id):
        run_cli(repo, "complete", t, "--force")
    st = fold(EventLog(repo).read_all(), strict=False)
    assert "2 ready to close" in PL.report(st, Config.load(repo), "T3")  # P1 and bugs


def test_off_suppresses_it_and_phase_shows_only_the_phase_and_next(repo):
    _project(repo)
    st = fold(EventLog(repo).read_all(), strict=False)
    cfg = Config.load(repo)
    assert PL.report(st, cfg, "T1", mode="off") == ""
    short = PL.report(st, cfg, "T1", mode="phase").splitlines()
    assert short[0].startswith("Phase P1:") and short[1].startswith("Next")
    assert len(short) == 2  # nothing else: no global Progress line
    assert run_cli(repo, "config", "session.progress_after_complete", "off")[0] == 0
    out = run_cli(repo, "complete", "T1", "--force")[1]
    assert "Progress:" not in out and "Phase P1:" not in out and "Next" not in out


def test_json_carries_the_block(repo):
    _project(repo)
    body = json.loads(run_cli(repo, "--json", "complete", "T1", "--force")[1])
    assert body["progress"].startswith("Progress:")


def test_ready_work_held_by_the_cap_is_named_not_reported_as_nothing(repo):
    _project(repo)
    cfg = Config.load(repo)
    cfg.schedule.max_parallel_tasks = 0
    st = fold(EventLog(repo).read_all(), strict=False)
    text = PL.report(st, cfg, "T1")
    assert "Next (when a slot frees): " in text and "nothing ready" not in text
