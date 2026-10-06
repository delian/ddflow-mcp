"""Event triggers (B-trigger-model; D-sched-triggers-separate, D-trigger-actions-create-items).

A trigger counts matching events and, when N arrive within a window, FILES a queue item
from a scheduled job's template -- never runs an agent. Every met condition is recorded,
fired or suppressed with the reason. These tests pin the definition rules, each storm
limit (debounce, cooldown, one open remediation per key, the per-trigger cap, the global
hourly cap, the hop limit, the circuit breaker), that a trigger never reads its own
output, and what the evaluator writes.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import schedule as A
from ddflow.core.events import Event
from ddflow.core.model import HANDLERS, Schedule, fold
from ddflow.infra.log import EventLog
from ddflow.services import triggers as TR

OK, FAIL, NOTHING = 0, 1, 2
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
KINDS = ("trigger.evaluated", "trigger.fired", "trigger.suppressed")


def _at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


_n = iter(range(1, 10_000))


def _ev(kind: str, subject: str, minutes: float, **data) -> Event:
    n = next(_n)
    return Event(
        kind=kind, subject=subject, data=data, lamport=n, agent="a", ts=_at(minutes), id=f"e{n}"
    )


def _trig(**kw) -> TR.Trigger:
    base = {"event": "gate.failed", "action": {"job": "fix"}, "enabled": True, "cooldown": 0}
    return TR.Trigger(id=kw.pop("id", "t"), **{**base, **kw})


def _run(events: list[Event], trig: TR.Trigger, at: float, **kw) -> list[TR.Decision]:
    return TR.evaluate(fold(events), events, {trig.id: trig}, T0 + timedelta(minutes=at), **kw)


def _fired(item: str, key: str, minutes: float, *, trig="t", hop=1, digest="") -> list[Event]:
    return [
        _ev("task.added", item, minutes, title="remediation"),
        _ev("trigger.fired", trig, minutes, key=key, items=[item], hop=hop, digest=digest),
    ]


# -- definitions ---------------------------------------------------------------------------


def test_the_three_kinds_fold_strictly():
    assert set(KINDS) <= set(HANDLERS)
    fold(
        [_ev(k, "t", i, key="k", items=["I"], reason="disabled") for i, k in enumerate(KINDS)],
        strict=True,
    )


@pytest.mark.parametrize(
    ("spec", "says"),
    [
        ({"action": {"job": "fix"}}, "event is required"),
        ({"event": "gate.failed"}, "action is required"),
        ({"event": "trigger.*", "action": {"job": "fix"}}, "never reads its own output"),
        ({"event": "*", "action": {"job": "fix"}}, "never reads its own output"),
        ({"event": "gate.failed", "action": {"job": "fix", "run": "x"}}, "action must be"),
        ({"event": "gate.failed", "action": {"job": "fix"}, "count": 0}, "count must be"),
        ({"event": "gate.failed", "action": {"job": "fix"}, "key": "{who}"}, "key field {who}"),
        ({"event": "gate.failed", "action": {"job": "fix"}, "on": "x"}, "unknown field(s) on"),
        ({"event": "gate.failed", "action": {"job": "fix"}, "enabled": "yes"}, "enabled must"),
    ],
)
def test_a_bad_trigger_is_refused_and_says_why(spec, says):
    trig, errors = TR.build("t", spec)
    assert trig is None and any(says in e for e in errors), errors


def test_a_trigger_starts_disabled():
    trig, errors = TR.build("t", {"event": "gate.failed", "action": {"job": "fix"}})
    assert not errors and trig is not None and trig.enabled is False


@pytest.fixture
def proj(repo: Path) -> Path:
    code, out, err = run_cli(repo, "init")
    assert code == 0, (code, out, err)
    assert (
        A.schedule_define(
            repo,
            "fix",
            {"title": "Fix it", "cadence": {"every_days": 7}, "scope_globs": ["src/**"]},
        ).exit
        == OK
    )
    return repo


def _file(repo: Path, name: str, text: str) -> None:
    d = repo / ".ddflow" / "triggers"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


def test_a_trigger_file_loads_and_a_broken_one_is_reported(proj):
    _file(proj, "ci.toml", 'event = "ci.result"\naction = { job = "fix" }\nenabled = true\n')
    _file(proj, "nojob.toml", 'event = "ci.result"\naction = { job = "ghost" }\n')
    _file(proj, "bad.toml", "event = [\n")
    out = A.trigger_list(proj)
    assert [r["id"] for r in out.data["rows"]] == ["ci"]
    errors = " | ".join(out.data["errors"])
    assert "nojob.toml: action.job 'ghost' is not a scheduled job" in errors
    assert "bad.toml: cannot read it" in errors


# -- the condition -------------------------------------------------------------------------


def test_n_events_within_the_window_fire_and_fewer_or_older_do_not():
    trig = _trig(count=3, window=60)
    evs = [_ev("gate.failed", "T1", m) for m in (0, 10, 20)]
    [d] = _run(evs, trig, 30)
    assert d.fire and d.key == "" and len(d.events) == 3
    assert _run(evs[:2], trig, 30) == []
    # the window bounds how far apart the events are, not how old they are now
    assert _run(evs, trig, 70)[0].fire
    apart = [_ev("gate.failed", "T1", m) for m in (0, 10, 61)]
    assert _run(apart, trig, 70) == []


def test_the_key_and_the_match_split_and_filter_events():
    trig = _trig(count=2, key="{data.gate}", match={"subject": "T*"})
    evs = [
        _ev("gate.failed", "T1", 0, gate="unit_tests"),
        _ev("gate.failed", "T2", 1, gate="unit_tests"),
        _ev("gate.failed", "T3", 2, gate="critic"),
        _ev("gate.failed", "X9", 3, gate="critic"),
    ]
    [d] = _run(evs, trig, 5)
    assert (d.key, d.fire) == ("unit_tests", True)


def test_trigger_events_are_never_a_source():
    trig = _trig(event="t*", count=1)
    evs = [_ev("trigger.fired", "other", 0, key="", items=[]), _ev("task.added", "T1", 1)]
    assert [d.events for d in _run(evs, trig, 5)] == [[evs[1].id]]


# -- the storm limits ----------------------------------------------------------------------


def test_disabled_is_recorded_as_suppressed_not_ignored():
    [d] = _run([_ev("gate.failed", "T1", 0)], _trig(enabled=False), 5)
    assert (d.fire, d.reason) == (False, "disabled")


def test_debounce_waits_for_quiet():
    trig = _trig(debounce=10)
    evs = [_ev("gate.failed", "T1", 0)]
    assert _run(evs, trig, 5)[0].reason == "debounce"
    assert _run(evs, trig, 11)[0].fire


def test_only_events_after_the_last_fire_count_and_cooldown_holds_the_key():
    trig = _trig(cooldown=60)
    evs = [_ev("gate.failed", "T1", 0), *_fired("R1", "", 1)]
    evs += [_ev("item.completed", "R1", 2)]
    assert _run(evs, trig, 3) == []  # the event before the fire is spent
    evs += [_ev("gate.failed", "T1", 5)]
    assert _run(evs, trig, 10)[0].reason == "cooldown"
    assert _run(evs, trig, 62)[0].fire


def test_one_open_remediation_per_key_and_the_per_trigger_cap():
    trig = _trig(key="{subject}", max_open=2)
    evs = [*_fired("R1", "T1", 0), *_fired("R2", "T2", 0)]
    evs += [_ev("gate.failed", s, 5) for s in ("T1", "T3")]
    by = {d.key: d for d in _run(evs, trig, 10)}
    assert by["T1"].reason == "open" and "R1" in by["T1"].detail
    assert by["T3"].reason == "max_open"


def test_a_trigger_never_counts_events_about_its_own_items():
    trig = _trig()
    evs = [*_fired("R1", "", 0), _ev("item.completed", "R1", 1), _ev("gate.failed", "R1", 2)]
    assert _run(evs, trig, 5) == []


def test_the_hop_limit_stops_a_remediation_chain():
    first = _fired("R1", "", 0, trig="other", hop=1)
    evs = [*first, _ev("gate.failed", "R1", 1)]
    [d] = _run(evs, _trig(hop_limit=1), 5)
    assert (d.reason, d.hop) == ("hop_limit", 2)
    assert _run(evs, _trig(hop_limit=2), 5)[0].fire


def test_the_breaker_holds_after_k_failed_or_empty_remediations_until_redefined():
    trig = _trig(breaker=2)
    dg = trig.digest()
    evs = [*_fired("R1", "", 0, digest=dg), _ev("item.abandoned", "R1", 1)]
    evs += [*_fired("R2", "", 2, digest=dg), _ev("item.completed", "R2", 3)]  # no merge: zero
    evs += [_ev("gate.failed", "T1", 5)]
    d = _run(evs, trig, 10)[0]
    assert d.reason == "breaker"
    assert _run(evs, _trig(breaker=2, count=1, window=600), 10)[0].fire  # a changed definition


def test_the_global_hourly_cap_spans_triggers():
    evs = []
    for i in range(3):
        evs += _fired(f"R{i}", f"k{i}", i, trig="other")
        evs += [_ev("item.completed", f"R{i}", i)]
    evs += [_ev("gate.failed", "T1", 10)]
    assert _run(evs, _trig(), 20, max_per_hour=3)[0].reason == "global_cap"
    assert _run(evs, _trig(), 20, max_per_hour=4)[0].fire


# -- the evaluator writes ------------------------------------------------------------------


def _log(repo: Path) -> list:
    return EventLog(repo, "t").read_all()


def test_evaluate_files_the_jobs_item_and_records_every_decision(proj):
    _file(
        proj,
        "red.toml",
        'event = "gate.failed"\nkey = "{subject}"\naction = { job = "fix" }\nenabled = true\n',
    )
    _file(proj, "off.toml", 'event = "gate.failed"\naction = { job = "fix" }\n')
    code, out, err = run_cli(proj, "task", "add", "T1", "--globs", "a.py")
    assert code == 0, (code, out, err)
    code, out, err = run_cli(
        proj, "gate", "record", "T1", "unit_tests", "--outcome", "failed", "--reason", "red"
    )
    assert code == 0, (code, out, err)
    now = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    assert A.trigger_evaluate(proj, now=now, dry_run=True).data["decisions"]
    assert not [e for e in _log(proj) if e.kind.startswith("trigger.")]
    out = A.trigger_evaluate(proj, now=now)
    assert out.exit == OK, out
    st = fold(_log(proj))
    [item] = [i for i in st.items if i.startswith("T-red-")]
    it = st.items[item]
    assert it.globs == ["src/**"] and "trigger:red" in it.tags and "schedule:fix" in it.tags
    assert st.trigger_items[item] == {"trigger": "red", "key": "T1", "hop": 1}
    assert [s["reason"] for s in st.trigger_suppressed["off"]] == ["disabled"]
    assert len(st.trigger_runs) == 1
    # the next run: the key's remediation is open, and that is recorded too
    again = A.trigger_evaluate(proj, now=now)
    assert again.exit == NOTHING or all(not d["fire"] for d in again.data["decisions"])
    shown = A.trigger_show(proj, "red").data
    assert shown["open"] == [item] and shown["fires"] == 1


def test_a_bad_now_is_a_failure(proj):
    assert A.trigger_evaluate(proj, now="yesterday").exit == FAIL


def test_nothing_met_is_exit_2_and_the_run_is_still_recorded(proj):
    out = A.trigger_evaluate(proj)
    assert out.exit == NOTHING
    assert [e.kind for e in _log(proj) if e.kind.startswith("trigger.")] == ["trigger.evaluated"]


def test_show_names_an_unknown_or_broken_trigger(proj):
    _file(proj, "bad.toml", 'event = "x"\n')
    out = A.trigger_show(proj, "bad")
    assert out.exit == FAIL and "action is required" in out.reason
    assert A.trigger_show(proj, "nope").exit == FAIL


def test_item_for_uses_the_job_template():
    job = Schedule(id="fix", title="Fix it", scope_globs=["src/**"], mode="fix", prompt="p")
    d = TR.Decision("t", "T1", True, events=["e1"], hop=1)
    trig = _trig(action={"job": "fix", "phase": "P1"})
    item = TR.item_for(trig, job, d, set(), fold([]))
    assert item["id"] == "T-t-1"
    assert item["data"]["parent"] == "P1" and item["data"]["globs"] == ["src/**"]
    assert "mode:fix" in item["data"]["tags"]


def test_the_cli_verbs(proj, capsys):
    import argparse

    from ddflow.surfaces import cli
    from ddflow.surfaces.commands.schedule import add_schedule_parser
    from ddflow.surfaces.context import Ctx

    def run(*argv):
        p = cli.build_parser()
        sub = next(x for x in p._actions if isinstance(x, argparse._SubParsersAction))
        add_schedule_parser(sub)
        a = p.parse_args(["--repo", str(proj), *argv])
        code = int(a.fn(a, Ctx(a)))
        return code, capsys.readouterr().out

    _file(proj, "red.toml", 'event = "gate.failed"\naction = { job = "fix" }\n')
    code, out = run("schedule", "trigger")
    assert code == OK and "red" in out and "off" in out
    code, out = run("schedule", "trigger", "show", "red")
    assert code == OK and "disabled" in out
    code, out = run("schedule", "trigger", "evaluate", "--dry-run")
    assert code == NOTHING and "no trigger condition is met" in out


def test_the_window_is_anchored_on_the_events_so_a_debounce_does_not_starve_it():
    """rubber-duck on B-trigger-model: anchored on `now`, the older event aged out of a
    5-minute window during a 5-minute debounce, and the met condition never fired."""
    trig = _trig(count=2, window=5, debounce=5)
    evs = [_ev("gate.failed", "T1", 0), _ev("gate.failed", "T1", 1)]
    assert _run(evs, trig, 2)[0].reason == "debounce"
    assert _run(evs, trig, 7)[0].fire
    spread = [_ev("gate.failed", "T1", 0), _ev("gate.failed", "T1", 9)]
    assert _run(spread, trig, 20) == []  # never two within 5 minutes of each other


def test_the_filed_item_carries_the_key_and_the_triggers_own_tags():
    job = Schedule(id="fix", title="Fix it", mode="report")
    d = TR.Decision("t", "T1", True, events=["e1"], hop=1)
    item = TR.item_for(_trig(tags=["nightly"]), job, d, set(), fold([]))
    assert item["data"]["tags"] == [
        "trigger:t",
        "schedule:fix",
        "mode:report",
        "key:T1",
        "nightly",
    ]


def test_fires_keep_a_tail_but_every_keys_latest_fire_is_kept():
    """critic on B-trigger-model: the fire list grew without bound."""
    from ddflow.core.model import TRIGGER_FIRES_KEPT

    evs = []
    for i in range(TRIGGER_FIRES_KEPT + 5):
        evs += _fired(f"R{i}", f"k{i}", i)
    st = fold(evs)
    assert len(st.trigger_fires["t"]) == TRIGGER_FIRES_KEPT
    assert len(st.trigger_keys["t"]) == TRIGGER_FIRES_KEPT + 5
    # the oldest key's remediation is still open, and still holds its key
    evs += [_ev("gate.failed", "x", 500, k="k0")]
    [d] = _run(evs, _trig(key="{data.k}", max_open=10_000), 600)
    assert (d.reason, d.detail) == ("open", "R0")


def test_a_suppression_keeps_the_events_that_met_the_condition():
    evs = [_ev("trigger.suppressed", "t", 0, key="", reason="disabled", detail="d", events=["e9"])]
    assert fold(evs).trigger_suppressed["t"][0]["events"] == ["e9"]


def test_the_evaluator_decides_under_the_log_lock(proj, monkeypatch):
    """rubber-duck and roborev on B-trigger-model: the read and the decision were taken
    outside the lock, so two evaluators could both file for one key."""
    from ddflow.infra import log as L

    seen = []
    real = TR.evaluate

    def spy(*a, **k):
        seen.append(any(v for v in L._HELD.values()))
        return real(*a, **k)

    monkeypatch.setattr(TR, "evaluate", spy)
    A.trigger_evaluate(proj)
    assert seen == [True]
