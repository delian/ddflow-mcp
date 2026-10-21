"""Patch skew (B63635ddc6a, decision D-mu-bugfix-answers).

A log stamped by a higher PATCH of the running version line (same FORMAT_LEVEL, same minor,
no capability the running ddflow lacks) no longer refuses every older clone's writes: the
write goes through with a one-time warning and the event carries the `older_ddflow` mark.
A higher FORMAT_LEVEL, a higher minor or major, or a capability gap still raises SkewRefused
(exit 3). The knob keeps its spelling: `[upgrade].skew = refuse` (the default) refuses those
three and warns for a patch; `warn` and `off` are as before. Reads are never affected.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run_cli

import ddflow
from ddflow.core import version as V
from ddflow.core.events import OLDER_MARK, Event, SkewRefused
from ddflow.infra import log as LOG
from ddflow.infra.log import EventLog

RUNNING = "0.5.3"
FORMAT = 3


@pytest.fixture(autouse=True)
def running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ddflow, "__version__", RUNNING)
    monkeypatch.setattr(ddflow, "FORMAT_LEVEL", FORMAT)
    monkeypatch.setattr(LOG, "_SKEW_WARNED", set())


def _shard_write(repo: Path, agent: str, kind: str, data: dict, n: int = 1) -> None:
    shard = repo / ".ddflow" / "events"
    shard.mkdir(parents=True, exist_ok=True)
    ev = Event(
        kind=kind,
        subject="ddflow",
        data=data,
        agent=agent,
        lamport=n,
        ts="2099-01-01T00:00:00.000000Z",
    )
    ev = Event(**{**ev.__dict__, "id": ev.compute_id()})
    with (shard / f"{agent}.jsonl").open("a") as fh:
        fh.write(ev.to_json() + "\n")


def _stamp(repo: Path, version: str, fmt: int = FORMAT) -> None:
    """A stamp another clone's ddflow wrote, as a merge brings it into this log."""
    _shard_write(
        repo,
        "future",
        "ddflow.seen",
        {"version": version, "install": "installed", "format_level": fmt},
    )


def _phases(log: EventLog) -> list[Event]:
    return [e for e in log.read_all() if e.kind == "phase.added"]


def test_a_higher_patch_with_the_same_format_lets_the_older_code_write(
    repo: Path, capsys: pytest.CaptureFixture[str]
):
    _stamp(repo, "0.5.7")
    log = EventLog(repo, "me")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("phase.added", "P2", {"title": "p"})
    err = capsys.readouterr().err
    # one warning, not one per write, naming both versions
    assert err.count("patch skew") == 1 and "0.5.7" in err and RUNNING in err
    marks = [e.data.get(OLDER_MARK) for e in _phases(log)]
    assert marks == [RUNNING, RUNNING]


def test_the_cli_still_exits_3_for_a_major_skew_and_reads(repo: Path):
    # the subprocess runs the package's own version: a stamp far above it is a major skew
    _stamp(repo, "999.0.0")
    code, _out, err = run_cli(repo, "phase", "add", "P1", "--title", "p")
    assert code == 3, err
    assert run_cli(repo, "status")[0] == 0


def test_a_higher_format_level_still_refuses(repo: Path):
    _stamp(repo, "0.5.7", FORMAT + 1)
    with pytest.raises(SkewRefused) as exc:
        EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})
    assert exc.value.exit_code == 3


def test_the_same_version_with_a_higher_format_still_refuses(repo: Path):
    _stamp(repo, RUNNING, FORMAT + 1)
    with pytest.raises(SkewRefused):
        EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})


@pytest.mark.parametrize("stamped", ["0.6.0", "0.6.9", "1.0.0"])
def test_a_higher_minor_or_major_still_refuses(repo: Path, stamped: str):
    _stamp(repo, stamped)
    with pytest.raises(SkewRefused) as exc:
        EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})
    assert exc.value.exit_code == 3


def test_a_capability_gap_still_refuses_its_kind_even_for_a_patch(repo: Path):
    _stamp(repo, "0.5.7")
    _shard_write(
        repo,
        "future",
        "ddflow.capabilities",
        {"capability": "from-the-future", "kinds": ["phase.added"], "version": "0.5.7"},
        n=2,
    )
    log = EventLog(repo, "me")
    with pytest.raises(SkewRefused):
        log.append("phase.added", "P1", {"title": "p"})
    log.append("lease.renewed", "T1", {})  # a kind it does not govern is no direct conflict


def test_reads_are_unaffected_by_any_skew(repo: Path):
    _stamp(repo, "0.6.0")
    log = EventLog(repo, "me")
    assert list(log.read_all())


def test_a_patch_skew_needs_no_override_and_records_none(repo: Path):
    _stamp(repo, "0.5.7")
    log = EventLog(repo, "me")
    assert log.override_skew("not needed") is None
    log.append("phase.added", "P1", {"title": "p"})
    assert not [e for e in log.read_all() if e.kind == "skew.overridden"]


def test_an_explicit_refuse_policy_has_no_stricter_patch_setting(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """`refuse` is the default spelling: a patch skew warns under it, a minor skew refuses.
    There is no separate strict-patch value (decision D-mu-bugfix-answers)."""
    monkeypatch.setenv("DDFLOW_UPGRADE_SKEW", "refuse")
    _stamp(repo, "0.5.7")
    log = EventLog(repo, "me")
    log.append("phase.added", "P1", {"title": "p"})
    assert _phases(log)[0].data.get(OLDER_MARK) == RUNNING
    _stamp(repo, "0.6.0")
    with pytest.raises(SkewRefused):
        log.append("phase.added", "P2", {"title": "p"})


@pytest.mark.parametrize("policy", ["warn", "off"])
def test_the_other_policies_still_write_through_any_skew(
    repo: Path, monkeypatch: pytest.MonkeyPatch, policy: str
):
    monkeypatch.setenv("DDFLOW_UPGRADE_SKEW", policy)
    _stamp(repo, "0.6.0")
    EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})


@pytest.mark.parametrize(
    ("running_v", "highest", "ok"),
    [
        ("0.5.3", "0.5.7", True),
        ("0.5.3", "0.5.3", False),  # no skew at all: not a patch skew
        ("0.5.7", "0.5.3", False),  # the running one is the newer
        ("0.5.3", "0.6.0", False),
        ("0.5.3", "1.5.7", False),
        ("0.5.3", "0.5.7rc1", False),  # a pre-release is not a release patch
        ("0.5", "0.5.2", True),  # 0.5 is 0.5.0
        ("x", "0.5.7", False),  # an unknown version is never compared
    ],
)
def test_patch_only_skew_is_a_pure_comparison(running_v: str, highest: str, ok: bool):
    assert V.patch_only_skew(running_v, highest) is ok


def test_an_older_adopt_is_refused_by_a_log_a_newer_minor_wrote(repo: Path):
    """`adopt` rewrites tracked files without appending an event, so the log's guard has to
    be asked for it: an older ddflow must not downgrade what a newer one wrote."""
    from ddflow.api.setup import Adoption, setup

    _stamp(repo, "0.6.0")
    out = setup(repo, Adoption(refresh_docs=True))
    assert out.exit == 3, out
    assert not (repo / "AGENTS.md").exists()


def test_an_older_adopt_is_refused_even_by_a_patch_skew(repo: Path):
    """The managed text differs between patch releases (0.2.0 -> 0.2.1 reworded the driver
    doc), so the relaxation that lets log writes through does not cover re-rendering it."""
    from ddflow.api.setup import Adoption, setup

    _stamp(repo, "0.5.7")
    out = setup(repo, Adoption(agents="claude"))
    assert out.exit == 3, out
    assert not (repo / "AGENTS.md").exists()


def test_the_patch_skew_notice_is_said_once_across_processes(repo: Path):
    """B-path-ddflow-version-skew-noise: each CLI call is a new process, so a per-process
    set repeated the warning on every write; a machine-local marker says it once."""
    _stamp(repo, "0.5.7")
    LOG._SKEW_WARNED.clear()
    EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})
    LOG._SKEW_WARNED.clear()  # a new process
    EventLog(repo, "me").append("phase.added", "P2", {"title": "p"})
    assert (repo / ".ddflow" / "local" / "skew-notice.json").exists()


def test_the_patch_skew_notice_names_the_fix_and_the_quiet_knob(
    repo: Path, capsys: pytest.CaptureFixture[str]
):
    _stamp(repo, "0.5.7")
    EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})
    err = capsys.readouterr().err
    assert "skew_notice" in err and "0.5.7" in err and "pgrade" in err


def test_a_second_process_stays_quiet_and_always_repeats(
    repo: Path, capsys: pytest.CaptureFixture[str]
):
    _stamp(repo, "0.5.7")
    EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})
    capsys.readouterr()
    LOG._SKEW_WARNED.clear()
    EventLog(repo, "me").append("phase.added", "P2", {"title": "p"})
    assert capsys.readouterr().err == ""


def test_skew_notice_always_repeats_per_process_and_off_is_silent(
    repo: Path, capsys: pytest.CaptureFixture[str]
):
    _stamp(repo, "0.5.7")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[upgrade]\nskew_notice = "off"\n')
    EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})
    assert capsys.readouterr().err == ""
    cfg.write_text('[upgrade]\nskew_notice = "always"\n')
    LOG._SKEW_WARNED.clear()
    EventLog(repo, "me").append("phase.added", "P2", {"title": "p"})
    assert "patch skew" in capsys.readouterr().err
    LOG._SKEW_WARNED.clear()
    EventLog(repo, "me").append("phase.added", "P3", {"title": "p"})
    assert "patch skew" in capsys.readouterr().err


def test_pins_regenerate_refuses_an_older_binary_than_the_log(repo: Path):
    from ddflow.api import pin_files as PF

    _stamp(repo, "0.5.7")  # a PATCH ahead: appends warn, but pin output differs between patches
    out = PF.pins_regenerate(repo, start=repo, agent="me")
    assert out.exit == 3 and "0.5.7" in out.reason and RUNNING in out.reason
    # a check (read only) is not refused
    assert PF.pins_regenerate(repo, start=repo, agent="me", check=True).exit != 3


def test_a_malformed_marker_only_repeats_the_notice(repo: Path, capsys: pytest.CaptureFixture[str]):
    _stamp(repo, "0.5.7")
    marker = repo / ".ddflow" / "local" / "skew-notice.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    for bad in ('{"seen": null}', '{"seen": 3}', "[1]", "not json"):
        marker.write_text(bad)
        LOG._SKEW_WARNED.clear()
        EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})
        assert "patch skew" in capsys.readouterr().err


def test_two_projects_in_one_process_each_get_their_notice(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    import shutil

    other = tmp_path / "other"
    shutil.copytree(repo, other)
    _stamp(repo, "0.5.7")
    _stamp(other, "0.5.7")
    EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})
    EventLog(other, "me").append("phase.added", "P1", {"title": "p"})
    assert capsys.readouterr().err.count("patch skew") == 2


def test_a_relative_root_is_told_apart_by_where_it_points(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    import shutil

    a, b = tmp_path / "wa", tmp_path / "wb"
    for d in (a, b):
        d.mkdir()
        shutil.copytree(repo, d / "proj")
        _stamp(d / "proj", "0.5.7")
    for d in (a, b):
        monkeypatch.chdir(d)
        EventLog(Path("proj"), "me").append("phase.added", "P1", {"title": "p"})
    assert capsys.readouterr().err.count("patch skew") == 2
