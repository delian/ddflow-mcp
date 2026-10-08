"""Mixed-version clones (B-uni-compat-capabilities, decision D-compat 2).

Slice 1, the format level: the skew guard keys on the version AND on FORMAT_LEVEL
(`ddflow/__init__.py`), so a branch or source tree that changes an on-disk format without a
version bump is still refused the writes its format cannot make safely.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run_cli

import ddflow
from ddflow.core import version as V
from ddflow.core.events import Event, SkewRefused, stamp_facts
from ddflow.infra.log import EventLog, skew_message


def _stamp(repo: Path, version: str, fmt: int | None, agent: str = "future", n: int = 1) -> None:
    """A stamp another clone's ddflow wrote, as a merge brings it into this log."""
    shard = repo / ".ddflow" / "events"
    shard.mkdir(parents=True, exist_ok=True)
    data: dict = {"version": version, "install": "installed"}
    if fmt is not None:
        data["format_level"] = fmt
    ev = Event(
        kind="ddflow.seen",
        subject="ddflow",
        data=data,
        agent=agent,
        lamport=n,
        ts="2099-01-01T00:00:00.000000Z",
    )
    ev = Event(**{**ev.__dict__, "id": ev.compute_id()})
    with (shard / f"{agent}.jsonl").open("a") as fh:
        fh.write(ev.to_json() + "\n")


def _seen(log: EventLog) -> list[Event]:
    return [e for e in log.read_all() if e.kind == "ddflow.seen"]


# -- the stamp carries the format ---------------------------------------------------------


def test_the_stamp_records_the_format_level_it_was_written_at(repo: Path):
    log = EventLog(repo, "a1")
    log.append("phase.added", "P1", {"title": "p"})
    (stamp,) = _seen(log)
    assert stamp.data["format_level"] == ddflow.FORMAT_LEVEL


def test_one_reader_of_the_running_format_level(monkeypatch):
    assert V.format_level() == ddflow.FORMAT_LEVEL
    monkeypatch.setattr(ddflow, "FORMAT_LEVEL", 7)
    assert V.format_level() == 7
    monkeypatch.setattr(ddflow, "FORMAT_LEVEL", "x")
    assert V.format_level() == 0  # not a level: never a reason to refuse


# -- the guard ----------------------------------------------------------------------------


def test_a_log_ahead_in_format_refuses_a_writer_of_the_same_version(repo: Path):
    _stamp(repo, ddflow.__version__, ddflow.FORMAT_LEVEL + 1)
    log = EventLog(repo, "me")
    with pytest.raises(SkewRefused) as exc:
        log.append("phase.added", "P1", {"title": "p"})
    msg = str(exc.value)
    assert exc.value.exit_code == 3
    assert f"data format level {ddflow.FORMAT_LEVEL + 1}" in msg
    assert f"writes level {ddflow.FORMAT_LEVEL}" in msg
    assert "--allow-older-version" in msg
    assert not (repo / ".ddflow" / "events" / "me.jsonl").exists()  # not even a stamp


def test_the_cli_refuses_with_exit_3_and_still_reads(repo: Path):
    _stamp(repo, ddflow.__version__, ddflow.FORMAT_LEVEL + 1)
    code, out, err = run_cli(repo, "phase", "add", "P1", "--title", "p")
    assert code == 3, (out, err)
    assert "data format level" in err
    assert run_cli(repo, "status")[0] == 0


def test_a_stamp_without_a_format_level_says_nothing_about_format(repo: Path):
    _stamp(repo, ddflow.__version__, None)
    log = EventLog(repo, "me")
    log.append("phase.added", "P1", {"title": "p"})  # no refusal
    assert [e.data.get("format_level") for e in _seen(log) if e.agent == "me"] == [
        ddflow.FORMAT_LEVEL
    ]


def test_an_equal_or_lower_format_is_not_skew(repo: Path):
    _stamp(repo, ddflow.__version__, ddflow.FORMAT_LEVEL)
    EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})


def test_a_version_skew_keeps_its_own_message(repo: Path):
    _stamp(repo, "99.0.0", ddflow.FORMAT_LEVEL)
    with pytest.raises(SkewRefused) as exc:
        EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})
    assert "data format level" not in str(exc.value)
    assert "Upgrade ddflow-mcp to >= 99.0.0" in str(exc.value)


def test_policy_warn_and_off_never_refuse_a_format_skew(repo: Path):
    _stamp(repo, ddflow.__version__, ddflow.FORMAT_LEVEL + 1)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.parent.mkdir(exist_ok=True)
    for policy in ("warn", "off"):
        cfg.write_text(f'[upgrade]\nskew = "{policy}"\n')
        EventLog(repo, f"me-{policy}").append("phase.added", f"P-{policy}", {"title": "p"})


# -- the override is against a format ------------------------------------------------------


def test_an_override_covers_that_format_and_a_higher_one_is_refused_again(repo: Path):
    _stamp(repo, ddflow.__version__, ddflow.FORMAT_LEVEL + 1)
    log = EventLog(repo, "me")
    ov = log.override_skew("operator said go")
    assert ov is not None
    assert ov.data["log_format"] == ddflow.FORMAT_LEVEL + 1
    log.append("phase.added", "P1", {"title": "p"})
    assert log.read_all()[-1].data["older_ddflow"] == ddflow.__version__
    _stamp(repo, ddflow.__version__, ddflow.FORMAT_LEVEL + 2, agent="future2")
    with pytest.raises(SkewRefused):
        log.append("phase.added", "P2", {"title": "p"})


def test_a_version_override_carries_no_format_and_a_format_skew_needs_its_own(repo: Path):
    _stamp(repo, "99.0.0", ddflow.FORMAT_LEVEL)
    log = EventLog(repo, "me")
    ov = log.override_skew("go")
    assert ov is not None and "log_format" not in ov.data
    log.append("phase.added", "P1", {"title": "p"})  # covered
    _stamp(repo, "99.0.0", ddflow.FORMAT_LEVEL + 1, agent="future2")
    with pytest.raises(SkewRefused):
        log.append("phase.added", "P2", {"title": "p"})  # the format moved on


def test_no_override_when_there_is_no_skew(repo: Path):
    assert EventLog(repo, "me").override_skew("why") is None


# -- the pure facts ------------------------------------------------------------------------


def _events(*stamps: tuple[str, int | None]) -> list[Event]:
    out = []
    for n, (version, fmt) in enumerate(stamps, 1):
        data: dict = {"version": version}
        if fmt is not None:
            data["format_level"] = fmt
        out.append(Event(kind="ddflow.seen", subject="ddflow", data=data, agent="x", lamport=n))
    return out


def test_facts_without_a_format_level_compare_versions_only():
    facts = stamp_facts(_events(("0.2.0", 9)), "me", "0.2.0")
    assert not facts.skewed and not facts.format_skewed and facts.highest_format == 9


def test_facts_name_the_highest_format_and_say_when_only_the_format_is_behind():
    facts = stamp_facts(_events(("0.2.0", 1), ("0.2.0", 3)), "me", "0.2.0", 2)
    assert facts.highest_format == 3 and facts.skewed and facts.format_skewed


def test_a_version_skew_is_not_also_called_a_format_skew():
    facts = stamp_facts(_events(("0.3.0", 3)), "me", "0.2.0", 2)
    assert facts.skewed and not facts.format_skewed


def test_bad_format_values_are_ignored():
    stamps = [("0.2.0", None), ("0.2.0", 0)]
    ev = [
        *_events(*stamps),
        Event(kind="ddflow.seen", subject="d", data={"version": "0.2.0", "format_level": True}),
    ]
    assert stamp_facts(ev, "me", "0.2.0", 1).highest_format == 0


def test_an_agent_stamps_again_when_only_its_format_changed():
    ev = _events(("0.2.0", 1))
    ev = [Event(**{**e.__dict__, "agent": "me"}) for e in ev]
    assert stamp_facts(ev, "me", "0.2.0", 1).seen_by_me
    assert not stamp_facts(ev, "me", "0.2.0", 2).seen_by_me
    assert stamp_facts(ev, "me", "0.2.0").seen_by_me  # no level asked: as before


def test_the_message_names_both_levels_only_for_a_format_only_skew():
    fmt = skew_message("0.2.0", "0.2.0", "w", log_format=3, format_level=2)
    assert "data format level 3" in fmt and "writes level 2" in fmt
    plain = skew_message("0.2.0", "0.3.0", "w")
    assert "format level" not in plain and "0.3.0" in plain
