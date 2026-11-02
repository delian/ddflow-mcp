"""B-dryrun-flake: a failure explains itself, and concurrent writers in the live project are
not mistaken for the rehearsal's doing.

Split from tests/test_upgrade_dry_run.py (rule r-small-files); helpers live in upgrade_dry_run_support.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from safe_cleanup import remove_owned
from upgrade_dry_run_support import (
    clean_fixture,  # noqa: F401  (registered as the module fixture `clean`)
    git,
    options,
    project,
    rows,
)

from ddflow.services import upgrade_dryrun as DR

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
    sysdir = tmp_path / "systmp"  # the "system temp directory" the fallback uses is ours alone
    sysdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(sysdir))
    report = DR.run_all(options(tmp_path, root, old=None, build_old=True, diag_dir=blocked / "d"))
    try:
        assert report.exit_code == 2 and report.diagnostics
        assert not report.diagnostics.startswith(str(blocked))  # fell back to the temp directory
        data = json.loads(Path(report.diagnostics).read_text("utf-8"))
        assert "PIP-STDERR" in json.dumps(data["commands"])
        assert data["facts"]["could_not_run"] == ["build the old ddflow from the clone"]
    finally:
        # only ever a directory the run made under the system temp dir; never '.' (B-e5e7dc7e27)
        remove_owned(
            Path(report.diagnostics).parent if report.diagnostics else None, tempfile.gettempdir()
        )


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
