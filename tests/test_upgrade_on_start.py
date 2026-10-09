"""B-upgrade.10-container-start: a server or container that starts newer than its project.

Decisions D-self-upgrade (5) and D-upgrade-on-mcp-connect. The project is one a real ddflow
0.1.3 built (tests/fixtures/releases/0.1.3), so the running ddflow is genuinely newer. No
network, no installer: the start works on the project's own files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api._base import _load
from ddflow.config import Config
from ddflow.services import upgrade_start as US

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "tests" / "fixtures" / "releases" / "0.1.3" / "project"
LOCAL = Path(".ddflow") / "local"


@pytest.fixture
def old(tmp_path: Path) -> Path:
    r = tmp_path / "old"
    shutil.copytree(OLD, r)
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    for k, v in (("user.email", "t@e.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "old"], check=True)
    return r


def _start(repo: Path, surf: str = "mcp", **env: str) -> dict:
    log, cfg, st = _load(repo, "starter")
    return US.start(repo, log, cfg, st, agent="starter", surf=surf, environ=env)


def _totals(repo: Path) -> dict[str, int]:
    _code, out, _err = run_cli(repo, "--json", "upgrade")
    return {k: len(v) for k, v in json.loads(out)["categories"].items()}


def _snapshot(repo: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(repo)): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file()
        and ".git" not in p.parts
        and "local" not in p.parts
        and not p.name.startswith("index.db")  # derived: rebuilt by a safe start
    }


def test_the_knobs_and_their_defaults() -> None:
    cfg = Config()
    assert cfg.upgrade.on_start == "safe"
    assert cfg.upgrade.start_timeout_s == 10.0


def test_a_container_start_applies_the_safe_categories_and_leaves_the_rest(old: Path) -> None:
    before = _totals(old)
    assert before["hooks"] and before["instructions"] and before["config"]
    rep = _start(old, "container")
    assert rep["status"] == US.APPLIED, rep
    assert "applied hooks and instructions" in rep["text"] and "backup" in rep["text"]
    after = _totals(old)
    assert after["hooks"] == 0 and after["instructions"] == 0
    assert after["config"] == before["config"], "config defaults are the operator's"
    assert "still pending" in rep["text"] and "Ask the operator" in rep["text"]
    _log, _cfg, st = _load(old, "starter")
    assert st.upgrades, "an upgrade.applied event was recorded"
    assert any((old / ".ddflow" / "backups").iterdir()), "the originals were saved first"


def test_an_mcp_connect_proposes_and_applies_nothing_without_the_operator(old: Path) -> None:
    before_plan, before_files = _totals(old), _snapshot(old)
    rep = _start(old, "mcp")
    assert "Ask the operator" in rep["text"] and "ddflow_upgrade" in rep["text"]
    assert _totals(old) == before_plan, "no category of the plan moved"
    assert _snapshot(old) == before_files, "no tracked or config file was written"
    assert _load(old, "starter")[2].upgrades == []


def test_check_proposes_and_writes_nothing_but_the_marker(old: Path) -> None:
    before_files = _snapshot(old)
    rep = _start(old, "container", DDFLOW_UPGRADE_ON_START="check")
    assert rep["status"] == US.PROPOSED and "Nothing has been applied" in rep["text"]
    assert _snapshot(old) == before_files
    assert not (old / ".ddflow" / "backups").exists()


def test_off_does_nothing_at_all(old: Path) -> None:
    before = _snapshot(old)
    rep = _start(old, "container", DDFLOW_UPGRADE_ON_START="off")
    assert rep["status"] == US.OFF and rep["text"] == ""
    assert _snapshot(old) == before and not (old / LOCAL / "upgrade-start.json").exists()


def test_the_config_knob_is_the_default_and_the_environment_wins(old: Path) -> None:
    cfg = Config()
    cfg.upgrade.on_start = "off"
    assert US.mode(cfg, {}) == "off"
    assert US.mode(cfg, {US.ENV: "safe"}) == "safe"
    assert US.mode(cfg, {US.ENV: " CHECK "}) == "check"


def test_an_unknown_switch_counts_as_the_strictest(old: Path) -> None:
    assert US.mode(Config(), {US.ENV: "yolo"}) == "check"
    rep = _start(old, "container", DDFLOW_UPGRADE_ON_START="yolo")
    assert rep["status"] == US.PROPOSED
    assert _totals(old)["hooks"], "nothing was applied"


def test_the_surface_follows_the_image(monkeypatch: pytest.MonkeyPatch) -> None:
    assert US.surface({}) == "mcp"
    assert US.surface({US.CONTAINER_ENV: "1"}) == "container"
    for no in ("0", "false", "No", "true", "yes", " ", ""):
        assert US.surface({US.CONTAINER_ENV: no}) == "mcp", no


def test_a_project_that_is_up_to_date_hears_nothing(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == 0
    rep = _start(repo, "container")
    assert rep["text"] == "" and rep["status"] == US.CURRENT
    assert not (repo / LOCAL / "upgrade-start.json").exists()


def test_a_directory_ddflow_never_adopted_is_left_alone(tmp_path: Path) -> None:
    rep = US.start(tmp_path, None, Config(), None, environ={})
    assert rep["text"] == "" and not (tmp_path / ".ddflow").exists()


def test_a_read_only_project_is_planned_reported_and_not_written(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(US, "_writable", lambda repo: False)
    before = _snapshot(old)
    rep = _start(old, "container")
    assert rep["status"] == US.READONLY and "not writable" in rep["text"]
    assert _snapshot(old) == before
    assert not (old / LOCAL / "upgrade-start.json").exists(), "not even the marker"
    assert not (old / ".ddflow" / "backups").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_a_directory_the_process_cannot_write_is_read_only(old: Path) -> None:
    (old / LOCAL).mkdir(parents=True, exist_ok=True)
    os.chmod(old / ".ddflow", 0o555)
    try:
        assert US._writable(old) is False
    finally:
        os.chmod(old / ".ddflow", 0o755)
    assert US._writable(old) is True


def test_a_failing_step_does_not_stop_the_start(old: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ddflow.services import upgrade_apply as UA

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(UA, "apply", boom)
    rep = _start(old, "container")
    assert rep["text"] and "could not apply" in rep["text"] and "disk on fire" in rep["text"]


def test_a_failing_plan_is_told_not_thrown(old: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ddflow.services import upgrade_plan as UP

    def boom(*a, **k):
        raise RuntimeError("plan exploded")

    monkeypatch.setattr(UP, "build", boom)
    rep = _start(old, "container")
    assert rep["status"] == US.FAILED and "plan exploded" in rep["text"]
    assert "serving anyway" in rep["text"]


def test_a_slow_start_is_bounded_and_left_pending(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(US, "_work", lambda *a, **k: time.sleep(5))
    log, cfg, st = _load(old, "starter")
    cfg.upgrade.start_timeout_s = 0.2
    t0 = time.monotonic()
    rep = US.start(old, log, cfg, st, surf="container", environ={})
    assert time.monotonic() - t0 < 3
    assert rep["status"] == US.TIMEOUT and "0.2s" in rep["text"] and "pending" in rep["text"]


def test_a_zero_timeout_skips_the_work(old: Path) -> None:
    log, cfg, st = _load(old, "starter")
    cfg.upgrade.start_timeout_s = 0
    assert US.start(old, log, cfg, st, surf="container", environ={})["status"] == US.TIMEOUT
    assert _totals(old)["hooks"]


def test_an_older_runtime_writes_nothing_and_says_restart(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log, cfg, st = _load(old, "starter")
    st.ddflow_versions = {"99.0.0": {"agents": [], "at": "", "install": ""}}
    before = _snapshot(old)
    rep = US.start(old, log, cfg, st, surf="container", environ={})
    assert rep["status"] == US.OLDER and "OLDER" in rep["text"] and "restart" in rep["text"]
    assert _snapshot(old) == before


def test_the_handshake_carries_the_report_and_the_first_brief_repeats_it_once(old: Path) -> None:
    from ddflow.api import setup as S
    from ddflow.surfaces.mcp import _instructions

    S._START_REPORTS.clear()
    text = _instructions(old, "starter")
    assert "newer than this project" in text and "Ask the operator" in text
    assert _instructions(old, "starter") == text, "asked twice, checked once"
    code, out, err = run_cli(old, "hooks", "session-start")
    assert code == 0, err
    assert out.count("newer than this project") == 1
    code, out, _ = run_cli(old, "hooks", "session-start")
    assert "newer than this project" not in out, "the brief says it once"
    assert "Upgraded ddflow" not in out, "the start report supersedes the one-line notice"


def test_an_unreadable_project_does_not_fail_the_handshake(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.api import setup as S
    from ddflow.surfaces.mcp import _instructions

    S._START_REPORTS.clear()
    monkeypatch.setattr(S, "_load", lambda *a, **k: (_ for _ in ()).throw(OSError("unreadable")))
    assert "serving anyway" in _instructions(old, "starter")


def test_a_proposal_the_operator_already_applied_is_not_replayed(old: Path) -> None:
    """Review finding: the marker survived a start that found the project current."""
    _start(old, "mcp")
    assert (old / LOCAL / "upgrade-start.json").is_file()
    run_cli(old, "upgrade", "--apply", "--reason", "by hand")  # the CLI, not the start
    log, cfg, st = _load(old, "starter")
    st.upgrades = [{"to": "99.0.0"}]  # the project is now at (or past) the running version
    rep = US.start(old, log, cfg, st, surf="mcp", environ={})
    assert rep["status"] == US.CURRENT
    assert US.take_for_brief(old) == ""


def test_the_brief_still_delivers_when_the_marker_cannot_be_rewritten(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _start(old, "mcp")

    def refuse(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(US.fsio, "atomic_write", refuse)
    assert "newer than this project" in US.take_for_brief(old)


def test_a_failing_start_report_does_not_silence_the_one_line_notice(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.api import setup as S
    from ddflow.surfaces.mcp import _instructions

    S._START_REPORTS.clear()
    monkeypatch.setattr(S.US, "start", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(S, "upgrade_start", lambda *a, **k: (_ for _ in ()).throw(OSError("y")))
    from ddflow import api

    monkeypatch.setattr(api, "upgrade_start", S.upgrade_start)
    assert "Upgraded ddflow" in _instructions(old, "starter")


def test_two_handshakes_at_once_make_one_start_check(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from ddflow.api import setup as S

    S._START_REPORTS.clear()
    calls: list[int] = []

    def slow(*a, **k):
        calls.append(1)
        time.sleep(0.3)
        return {"text": "once"}

    monkeypatch.setattr(S.US, "start", slow)
    out: list[str] = []
    threads = [threading.Thread(target=lambda: out.append(S.upgrade_start(old))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert out == ["once"] * 4 and len(calls) == 1


def test_applying_the_upgrade_drops_the_cached_proposal(old: Path) -> None:
    """Review finding: a reconnect in the same process repeated the pre-apply proposal."""
    from ddflow.api import setup as S

    S._START_REPORTS.clear()
    assert "still pending" in S.upgrade_start(old, agent="starter")
    out = S.upgrade(old, apply="hooks", agent="starter")
    assert out.exit in (0, 1, 3), out
    assert str(old.resolve()) not in S._START_REPORTS
    assert "still pending" in S.upgrade_start(old, agent="starter")
    S.upgrade(old, restore="latest", agent="starter")
    assert str(old.resolve()) not in S._START_REPORTS, "a restore changes the plan too"


def test_an_empty_report_never_marks_the_notice_as_said(old: Path) -> None:
    from ddflow.services import upgrade_notice as UN

    UN.told(old, "99.0.0", "")
    assert UN.noticed_version(old) == ""


def test_a_failed_start_check_is_retried_not_cached(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: one transient failure muted the start check for the whole process."""
    from ddflow.api import setup as S

    S._START_REPORTS.clear()
    real = S._load
    monkeypatch.setattr(S, "_load", lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
    assert "failed (locked)" in S.upgrade_start(old, agent="starter")
    monkeypatch.setattr(S, "_load", real)
    assert "newer than this project" in S.upgrade_start(old, agent="starter")


def test_a_failing_marker_write_does_not_lose_the_report(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.services import upgrade_notice as UN

    monkeypatch.setattr(UN, "told", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk")))
    assert "newer than this project" in _start(old, "mcp")["text"]


def test_the_image_documents_the_switch() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert "DDFLOW_UPGRADE_ON_START" in text
    assert "DDFLOW_UPGRADE_ON_START" in (ROOT / "README.md").read_text()
