"""Mixed-version clones (B-uni-compat-capabilities, decision D-compat 2).

Slice 2, capabilities: the first write that uses one records it (`ddflow.capabilities`), and a
writer that lacks a recorded capability is refused just the kinds it governs, with the release
that provides it.

Slice 1, the format level: the skew guard keys on the version AND on FORMAT_LEVEL
(`ddflow/__init__.py`), so a branch or source tree that changes an on-disk format without a
version bump is still refused the writes its format cannot make safely.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run_cli

import ddflow
from ddflow.core import events as E
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
    (added,) = [e for e in log.read_all() if e.kind == "phase.added"]
    assert added.data["older_ddflow"] == ddflow.__version__
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
        Event(
            kind="ddflow.seen", subject="ddflow", data={"version": "0.2.0", "format_level": True}
        ),
    ]
    assert stamp_facts(ev, "me", "0.2.0", 1).highest_format == 0


def test_an_agent_stamps_again_when_only_its_format_changed():
    ev = _events(("0.2.0", 1))
    ev = [Event(**{**e.__dict__, "agent": "me"}) for e in ev]
    assert stamp_facts(ev, "me", "0.2.0", 1).seen_by_me
    assert not stamp_facts(ev, "me", "0.2.0", 2).seen_by_me
    assert stamp_facts(ev, "me", "0.2.0").seen_by_me  # no level asked: as before


def test_the_format_only_message_names_the_stamp_that_carries_the_format_and_what_to_upgrade_to():
    msg = skew_message(
        "2.0.0",
        "3.0.0",  # the highest VERSION, stamped at a lower format
        "x",
        log_format=5,
        format_level=2,
        format_only=True,
        format_version="2.0.0",
        format_by="y",
    )
    assert "ddflow 2.0.0 (stamped by y) at data format level 5" in msg
    assert "writes level 2" in msg and "(stamped by x)" not in msg  # not misattributed
    # "upgrade to >= <this version>" would already be satisfied, so it is not what is asked
    assert "release that writes data format level 5 or higher" in msg
    assert "to >= 3.0.0" not in msg


def test_a_version_skew_that_is_also_a_format_skew_says_both():
    msg = skew_message("0.2.0", "0.3.0", "w", log_format=3, format_level=2)
    assert "Upgrade ddflow-mcp to >= 0.3.0" in msg
    assert "data format level (3) is ahead of this ddflow's too (2)" in msg
    plain = skew_message("0.2.0", "0.3.0", "w")
    assert "format level" not in plain


def test_the_log_names_the_stamp_that_carries_the_highest_format():
    ev = [
        Event(
            kind="ddflow.seen",
            subject="ddflow",
            data={"version": "3.0.0", "format_level": 1},
            agent="x",
            lamport=1,
        ),
        Event(
            kind="ddflow.seen",
            subject="ddflow",
            data={"version": "2.0.0", "format_level": 5},
            agent="y",
            lamport=2,
        ),
    ]
    facts = stamp_facts(ev, "me", "3.0.0", 2)
    assert (facts.highest, facts.highest_by) == ("3.0.0", "x")
    assert (facts.highest_format, facts.format_version, facts.format_by) == (5, "2.0.0", "y")


def test_a_refusal_for_a_format_only_skew_names_the_format_stamp(repo: Path):
    _stamp(repo, "0.0.1", ddflow.FORMAT_LEVEL + 3, agent="oldver")
    with pytest.raises(SkewRefused) as exc:
        EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})
    assert "ddflow 0.0.1 (stamped by oldver)" in str(exc.value)


def test_an_override_survives_this_ddflow_catching_up_on_the_format(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """The override was made against the log's format; raising THIS ddflow's own level does
    not change what the log holds, so it still covers the (version) skew that remains."""
    _stamp(repo, "99.0.0", ddflow.FORMAT_LEVEL + 1)
    log = EventLog(repo, "me")
    assert log.override_skew("go").data["log_format"] == ddflow.FORMAT_LEVEL + 1
    monkeypatch.setattr(ddflow, "FORMAT_LEVEL", ddflow.FORMAT_LEVEL + 1)
    log.append("phase.added", "P1", {"title": "p"})  # version still older: the override holds


def test_a_level_of_zero_is_no_format_and_never_refuses(repo: Path, monkeypatch):
    monkeypatch.setattr(ddflow, "FORMAT_LEVEL", 0)
    _stamp(repo, ddflow.__version__, 9)
    log = EventLog(repo, "me")
    log.append("phase.added", "P1", {"title": "p"})
    assert log.override_skew("nothing to override") is None
    mine = [e for e in _seen(log) if e.agent == "me"]
    assert mine and "format_level" not in mine[0].data


def test_the_warn_policy_names_the_format_and_says_it_again_for_a_higher_one(
    repo: Path, capsys: pytest.CaptureFixture[str]
):
    from ddflow.infra import log as L

    L._SKEW_WARNED.clear()
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text('[upgrade]\nskew = "warn"\n')
    _stamp(repo, ddflow.__version__, ddflow.FORMAT_LEVEL + 1)
    log = EventLog(repo, "me")
    log.append("phase.added", "P1", {"title": "p"})
    err = capsys.readouterr().err
    assert f"data format level {ddflow.FORMAT_LEVEL + 1}" in err
    _stamp(repo, ddflow.__version__, ddflow.FORMAT_LEVEL + 2, agent="future2")
    log.append("phase.added", "P2", {"title": "p"})
    assert f"data format level {ddflow.FORMAT_LEVEL + 2}" in capsys.readouterr().err


def test_a_lower_format_than_ours_is_not_skew(repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ddflow, "FORMAT_LEVEL", 3)
    _stamp(repo, ddflow.__version__, 2)
    EventLog(repo, "me").append("phase.added", "P1", {"title": "p"})


def test_a_version_override_with_a_format_ahead_too_records_the_format(repo: Path):
    _stamp(repo, "99.0.0", ddflow.FORMAT_LEVEL + 1)
    ov = EventLog(repo, "me").override_skew("go")
    assert ov.data["log_version"] == "99.0.0" and ov.data["log_format"] == ddflow.FORMAT_LEVEL + 1


def test_a_stamp_without_a_format_names_no_format_carrier():
    ev = [Event(kind="ddflow.seen", subject="ddflow", data={"version": "1.0"}, agent="me")]
    facts = stamp_facts(ev, "me", "1.0", 1)
    assert (facts.highest_format, facts.format_version, facts.format_by) == (0, "", "")


def test_a_version_skew_is_not_reported_as_format_only_even_with_a_higher_format_elsewhere():
    ev = [
        Event(
            kind="ddflow.seen",
            subject="ddflow",
            data={"version": "3.0", "format_level": 1},
            agent="x",
            lamport=1,
        ),
        Event(
            kind="ddflow.seen",
            subject="ddflow",
            data={"version": "2.0", "format_level": 5},
            agent="y",
            lamport=2,
        ),
    ]
    facts = stamp_facts(ev, "me", "1.0", 2)
    assert facts.skewed and not facts.format_skewed  # the running VERSION is older: not format-only
    assert facts.highest == "3.0" and facts.format_version == "2.0"


# -- slice 2: capabilities ----------------------------------------------------------------


def _record(repo: Path, name: str, kinds: list[str], version: str, agent="future", n=1) -> None:
    """A `ddflow.capabilities` event another clone wrote, as a merge brings it in."""
    shard = repo / ".ddflow" / "events"
    shard.mkdir(parents=True, exist_ok=True)
    ev = Event(
        kind="ddflow.capabilities",
        subject="ddflow",
        data={"capability": name, "kinds": kinds, "version": version},
        agent=agent,
        lamport=n,
        ts="2099-01-01T00:00:00.000000Z",
    )
    ev = Event(**{**ev.__dict__, "id": ev.compute_id()})
    with (shard / f"{agent}.jsonl").open("a") as fh:
        fh.write(ev.to_json() + "\n")


def _caps(log: EventLog) -> list[Event]:
    return [e for e in log.read_all() if e.kind == "ddflow.capabilities"]


def test_the_first_text_write_records_the_redaction_capability_once(repo: Path):
    log = EventLog(repo, "a1")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("phase.added", "P2", {"title": "q"})
    (rec,) = _caps(log)
    cap = E.CAPABILITIES[E.CAP_LOG_REDACTION]
    assert rec.data["capability"] == E.CAP_LOG_REDACTION
    assert rec.data["version"] == cap.since
    assert rec.data["kinds"] == sorted(cap.kinds)
    # the record precedes the write that used it
    kinds = [e.kind for e in log.read_all()]
    assert kinds.index("ddflow.capabilities") < kinds.index("phase.added")


def test_a_write_that_uses_no_capability_records_none(repo: Path):
    log = EventLog(repo, "a1")
    log.append("lease.renewed", "T1", {})
    assert _caps(log) == []


def test_a_configured_id_template_records_the_id_capability(repo: Path):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text('[ids]\nbug = "bug-{seq}"\n')
    log = EventLog(repo, "a1")
    log.append("bug.found", "bug-1", {"summary": "s"})
    assert {e.data["capability"] for e in _caps(log)} == {E.CAP_ID_TEMPLATE, E.CAP_LOG_REDACTION}


def test_default_templates_do_not_record_the_id_capability(repo: Path):
    log = EventLog(repo, "a1")
    log.append("bug.found", "B1", {"summary": "s"})
    assert {e.data["capability"] for e in _caps(log)} == {E.CAP_LOG_REDACTION}


def test_a_writer_lacking_a_recorded_capability_is_refused_just_its_kinds(repo: Path):
    _record(repo, "future-thing", ["bug.found"], "9.9.9")
    log = EventLog(repo, "me")
    with pytest.raises(SkewRefused) as exc:
        log.append("bug.found", "B1", {"summary": "s"})
    msg = str(exc.value)
    assert exc.value.exit_code == 3
    assert (
        "`future-thing`" in msg and "bug.found" in msg and "Upgrade ddflow-mcp to >= 9.9.9" in msg
    )
    assert "everything else proceeds" in msg
    log.append("lease.renewed", "T1", {})  # a kind it does not govern proceeds
    assert [e for e in log.read_all() if e.kind == "bug.found"] == []


def test_a_known_capability_whose_kinds_this_writer_lacks_is_refused(repo: Path):
    _record(repo, E.CAP_ID_TEMPLATE, ["bug.found", "gate.passed"], "9.9.9")
    with pytest.raises(SkewRefused):
        EventLog(repo, "me").append("gate.passed", "T1", {"gate": "x"})
    EventLog(repo, "me").append("bug.found", "B1", {"summary": "s"})  # it has this one


def test_the_cli_refuses_the_governed_write_and_still_reads(repo: Path):
    _record(repo, "future-thing", ["phase.added"], "9.9.9")
    code, _out, err = run_cli(repo, "phase", "add", "P1", "--title", "p")
    assert code == 3 and "future-thing" in err
    assert run_cli(repo, "status")[0] == 0


def test_policy_off_and_warn_never_refuse_a_capability_gap(repo: Path):
    _record(repo, "future-thing", ["phase.added"], "9.9.9")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.parent.mkdir(exist_ok=True)
    for policy in ("warn", "off"):
        cfg.write_text(f'[upgrade]\nskew = "{policy}"\n')
        EventLog(repo, f"me-{policy}").append("phase.added", f"P-{policy}", {"title": "p"})


def test_the_capability_fold_is_independent_of_event_order():
    def ev(kinds, v, agent, n):
        return Event(
            kind="ddflow.capabilities",
            subject="ddflow",
            data={"capability": "c", "kinds": kinds, "version": v},
            agent=agent,
            lamport=n,
        )

    a, b = ev(["x"], "0.1.9", "p", 1), ev(["y"], "0.1.10", "q", 2)
    t1, t2 = ev(["x"], "0.1.9", "alice", 3), ev(["x"], "0.1.9", "bob", 4)
    assert (
        stamp_facts([t1, t2], "me", "1.0").capabilities["c"].by
        == stamp_facts([t2, t1], "me", "1.0").capabilities["c"].by
    )
    one = stamp_facts([a, b], "me", "1.0").capabilities["c"]
    two = stamp_facts([b, a], "me", "1.0").capabilities["c"]
    assert one == two
    assert one.kinds == {"x", "y"} and one.version == "0.1.10" and one.by == "q"


def test_a_malformed_capability_event_is_ignored():
    bad = Event(kind="ddflow.capabilities", subject="ddflow", data={"capability": 3}, agent="x")
    assert stamp_facts([bad], "me", "1.0").capabilities == {}


def test_the_state_folds_the_recorded_capabilities(repo: Path):
    from ddflow.core.model import fold

    log = EventLog(repo, "a1")
    log.append("phase.added", "P1", {"title": "p"})
    st = fold(log.read_all())
    assert (
        st.capabilities[E.CAP_LOG_REDACTION]["version"] == E.CAPABILITIES[E.CAP_LOG_REDACTION].since
    )
