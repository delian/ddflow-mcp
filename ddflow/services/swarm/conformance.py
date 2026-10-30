"""Lane conformance: how far up the ladder a lane gets before it is trusted with real items.

A lane is graded by the HIGHEST level it passes, the checks run in order and a level counts
only when every one below it passed (D-swarm-answers; research swarm-orchestration 7.3):

  1 launch         the lane starts, reports ``started`` and stops cleanly
  2 claim          it calls the claim tool and the call did not fail
  3 heartbeat      it renews its lease
  4 gate_record    it records a gate outcome
  5 hook           its pre-tool hook fires (a probe the caller supplies; SW-lane-hooks)
  6 lease_stop     it stops working once its lease is revoked
  7 tool_accuracy  at least ``[swarm].conformance_min_accuracy`` of its tool calls are
                   well formed lean-core calls, over at least ``conformance_min_calls``

A check that could not run (the lane has no ``tool_events`` capability, no probe was given, too
few calls) is ``unavailable``: it ends the climb and is never counted as passed. The result is
data (``Report.to_data``, stored by ``conformance_io``) that the scheduler reads: a lane gets
items only at its passed level. The run uses stub agents and stub endpoints only; it never
needs a real key. Pure grading (``grade``) is separate from the live run (``observe``/``run``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...config import Config
from ..lane_driver import Lane, LaneEvent, LaunchSpec, Result

CHECKS = (
    "launch",
    "claim",
    "heartbeat",
    "gate_record",
    "hook",
    "lease_stop",
    "tool_accuracy",
)
LEVEL_NAMES = ("none", *CHECKS)
PASSED, FAILED, UNAVAILABLE = "passed", "failed", "unavailable"
_PREFIXES = ("mcp__ddflow__", "mcp__ddflow_", "ddflow_", "ddflow.")


@dataclass(frozen=True)
class Params:
    """The knobs of a run, read once from ``[swarm]`` and ``[lanes]``."""

    min_accuracy: float = 0.9
    min_calls: int = 5
    idle_s: float = 5.0
    lease_wait_s: float = 10.0
    tools: frozenset[str] = frozenset()
    max_age_s: int = 0
    max_events: int = 1000

    @classmethod
    def from_config(cls, cfg: Config) -> Params:
        sw = cfg.swarm
        return cls(
            min_accuracy=float(sw.conformance_min_accuracy),
            min_calls=int(sw.conformance_min_calls),
            idle_s=float(sw.conformance_idle_s),
            lease_wait_s=float(sw.conformance_lease_wait_s),
            tools=frozenset(norm_tool(t) for t in sw.conformance_tools),
            max_age_s=int(sw.conformance_max_age_s),
            max_events=int(cfg.lanes.event_buffer),
        )


@dataclass(frozen=True)
class Outcome:
    check: str
    status: str  #: passed | failed | unavailable
    evidence: str = ""


@dataclass(frozen=True)
class Report:
    lane: str
    level: int  #: highest passed check, in order; 0 = none
    outcomes: tuple[Outcome, ...]
    at: float = 0.0  #: epoch seconds the run finished

    @property
    def level_name(self) -> str:
        return LEVEL_NAMES[self.level]

    def to_data(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "level": self.level,
            "level_name": self.level_name,
            "at": self.at,
            "outcomes": [
                {"check": o.check, "status": o.status, "evidence": o.evidence}
                for o in self.outcomes
            ],
        }

    @classmethod
    def from_data(cls, d: dict[str, Any]) -> Report:
        outs = tuple(Outcome(o["check"], o["status"], o.get("evidence", "")) for o in d["outcomes"])
        return cls(str(d["lane"]), int(d["level"]), outs, float(d.get("at", 0.0)))


@dataclass
class Observed:
    """What a run saw of one lane. ``None`` for a probe means it could not be run."""

    started: bool | None = None  #: a ``started`` event was seen
    start_error: str = ""  #: why the lane could not start (a Result's detail)
    start_status: str = UNAVAILABLE  #: the Result status that said so: unavailable | failed
    tool_events: bool = False  #: the lane can report tool calls at all
    calls: list[dict[str, Any]] = field(default_factory=list)  #: tool event fields, in order
    stopped_clean: bool | None = None  #: stop returned ok; None = not confirmed
    hook: bool | None = None
    hook_note: str = ""
    lease_stop: bool | None = None
    lease_note: str = ""


def norm_tool(name: str) -> str:
    n = str(name).strip().lower()
    while True:  # `mcp__ddflow__ddflow_claim` carries two prefixes
        for p in _PREFIXES:
            if n.startswith(p):
                n = n[len(p) :]
                break
        else:
            return n


def _ok(call: dict[str, Any]) -> bool:
    return call.get("ok") is not False and not call.get("error")


def _named(calls: list[dict[str, Any]], tool: str, actions: tuple[str, ...] = ("",)) -> list:
    return [
        c
        for c in calls
        if norm_tool(c.get("name", "")) == tool and str(c.get("action") or "") in actions
    ]


def _need_tool(o: Observed, check: str) -> Outcome | None:
    if not o.tool_events:
        return Outcome(check, UNAVAILABLE, "the lane reports no tool events (capability missing)")
    return None


def _called(o: Observed, check: str, tool: str, actions: tuple[str, ...] = ("",)) -> Outcome:
    if (miss := _need_tool(o, check)) is not None:
        return miss
    hits = _named(o.calls, tool, actions)
    if not hits:
        return Outcome(check, FAILED, f"no {tool} call among {len(o.calls)} tool call(s)")
    if not any(_ok(c) for c in hits):
        return Outcome(check, FAILED, f"{len(hits)} {tool} call(s), every one failed")
    return Outcome(check, PASSED, f"{len(hits)} {tool} call(s)")


def _probe(check: str, value: bool | None, note: str, yes: str, no: str) -> Outcome:
    if value is None:
        return Outcome(check, UNAVAILABLE, note or "the probe could not run")
    return Outcome(check, PASSED if value else FAILED, note or (yes if value else no))


def _accuracy(o: Observed, p: Params) -> Outcome:
    if (miss := _need_tool(o, "tool_accuracy")) is not None:
        return miss
    n = len(o.calls)
    if n < p.min_calls or n == 0:
        return Outcome("tool_accuracy", UNAVAILABLE, f"{n} call(s) seen, {p.min_calls} needed")
    good = sum(1 for c in o.calls if norm_tool(c.get("name", "")) in p.tools and _ok(c))
    rate = good / n
    status = PASSED if rate >= p.min_accuracy else FAILED
    return Outcome(
        "tool_accuracy", status, f"{good}/{n} well formed ({rate:.2f}, need {p.min_accuracy:.2f})"
    )


def grade(lane: str, o: Observed, p: Params, now: float | None = None) -> Report:
    """Pure: the report for what was observed. Every check gets an outcome; the level is the
    count of leading passes (a failed or unavailable check ends the climb)."""
    if o.started is None:
        status = FAILED if o.start_status == "failed" else UNAVAILABLE
        launch = Outcome("launch", status, o.start_error or "the lane could not be launched")
    elif not o.started:
        launch = Outcome("launch", FAILED, "no `started` event")
    elif o.stopped_clean is False:
        launch = Outcome("launch", FAILED, "the lane did not stop cleanly")
    elif o.stopped_clean is None:
        launch = Outcome("launch", UNAVAILABLE, "a clean stop was not confirmed")
    else:
        launch = Outcome("launch", PASSED, "started and stopped")
    outcomes = (
        launch,
        _called(o, "claim", "claim"),
        _called(o, "heartbeat", "heartbeat"),
        _called(o, "gate_record", "gate", ("", "record")),
        _probe("hook", o.hook, o.hook_note, "the hook fired", "the hook did not fire"),
        _probe("lease_stop", o.lease_stop, o.lease_note, "stopped at the lease", "kept working"),
        _accuracy(o, p),
    )
    level = 0
    for out in outcomes:
        if out.status != PASSED:
            break
        level += 1
    return Report(lane, level, outcomes, time.time() if now is None else now)


def _collect(driver: Lane, h: Any, idle_s: float, limit: int, deadline: float | None = None):
    n = 0
    for ev in driver.events(h, idle_s=idle_s):
        yield ev
        n += 1
        if n >= limit or (deadline is not None and time.monotonic() >= deadline):
            return


def _record(o: Observed, ev: LaneEvent) -> None:
    if ev.event == "started":
        o.started = True
    elif ev.event == "tool":
        o.calls.append(dict(ev.fields))


def _shown(o: Observed, p: Params) -> bool:
    """Enough seen: the lease actions were called and the accuracy sample is full."""
    names = {norm_tool(c.get("name", "")) for c in o.calls}
    return (
        bool(o.started) and {"claim", "heartbeat", "gate"} <= names and len(o.calls) >= p.min_calls
    )


def _alive(driver, h) -> bool:
    return driver.status(h).state in ("starting", "running")


def _lease_phase(driver, h, revoke: Callable[[], None], o: Observed, p: Params) -> None:
    """Revoke the lease of a lane that is demonstrably still working, then give it
    ``lease_wait_s`` to stop. Calls already in flight may land; working past the wait fails."""
    settle = time.monotonic() + p.idle_s / 10  # a lane about to exit by itself proves nothing
    while _alive(driver, h) and time.monotonic() < settle:
        time.sleep(0.02)
    if not _alive(driver, h):
        o.lease_note = "the lane ended before its lease was revoked; nothing was exercised"
        return
    try:
        revoke()
    except Exception as e:  # a probe's failure is data, not a crash
        o.lease_note = f"revoking the lease failed: {type(e).__name__}"
        return
    deadline = time.monotonic() + p.lease_wait_s
    after = 0
    while _alive(driver, h) and time.monotonic() < deadline:
        for ev in _collect(driver, h, 0.1, p.max_events, deadline):
            _record(o, ev)
            after += ev.event == "tool"
    o.lease_stop = not _alive(driver, h)
    o.lease_note = (
        f"stopped within the wait ({after} tool call(s) after the revocation)"
        if o.lease_stop
        else f"still working {p.lease_wait_s:g}s after the lease was revoked"
    )


def observe(
    driver: Lane,
    spec: LaunchSpec,
    p: Params,
    *,
    hook_probe: Callable[[], bool | None] | None = None,
    revoke: Callable[[], None] | None = None,
) -> Observed:
    """Start the lane (a stub agent on stub endpoints), collect its events, run the probes, stop it."""
    o = Observed()
    h = driver.start(spec)
    if isinstance(h, Result):
        o.start_error, o.start_status = f"{h.status}: {h.detail}", h.status
        return o
    o.started = False
    o.tool_events = "tool_events" in h.capabilities
    try:
        for ev in _collect(driver, h, p.idle_s, p.max_events):
            _record(o, ev)
            if _shown(o, p):
                break  # a lane that keeps working is not waited out
        if hook_probe is None:
            o.hook_note = "no hook probe was given"
        else:
            try:
                o.hook = hook_probe()
            except Exception as e:
                o.hook_note = f"the hook probe failed: {type(e).__name__}"
        if revoke is None:
            o.lease_note = "no lease revocation was given"
        else:
            _lease_phase(driver, h, revoke, o, p)
    finally:
        stop = driver.stop(h)
        o.stopped_clean = None if stop.status == "unavailable" else stop.ok
    return o


def run(
    lane: str,
    driver: Lane,
    spec: LaunchSpec,
    p: Params,
    *,
    hook_probe: Callable[[], bool | None] | None = None,
    revoke: Callable[[], None] | None = None,
) -> Report:
    """Observe, then grade."""
    return grade(lane, observe(driver, spec, p, hook_probe=hook_probe, revoke=revoke), p)


def admits(report: Report | None, needed: int | str, p: Params, now: float | None = None) -> bool:
    """May the lane take an item that needs conformance level ``needed`` (number or name)?
    No report, a lower level or an expired run (``max_age_s``) is no."""
    if report is None:
        return False
    if isinstance(needed, str) and needed not in LEVEL_NAMES:
        return False  # an unknown level name admits nothing
    need = LEVEL_NAMES.index(needed) if isinstance(needed, str) else int(needed)
    if p.max_age_s and (time.time() if now is None else now) - report.at > p.max_age_s:
        return False
    return report.level >= need
