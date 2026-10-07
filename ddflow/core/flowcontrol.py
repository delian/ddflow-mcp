"""Adaptive flow control: how many agents may be in flight right now.

A pure controller. It reads nothing and keeps no state of its own: every input
(samples of host and queue signals, the history of how many items were in flight, the
current time) arrives as an argument, and the answer is rebuilt by replaying the sample
ring from the start value. The same inputs therefore always give the same
:class:`Decision`, and a restart loses nothing.

The shape is additive-increase / multiplicative-decrease with hysteresis:

* **Increase** by one only after ``adapt_up_after_s`` of healthy samples (every available
  signal at or under its LOW mark) during which the limit was actually binding, that is
  the in-flight count had reached it. Raising a limit nobody touches proves nothing.
* **Decrease** to ``max(floor, floor(limit * decrease_factor))`` only when some signal is
  over its HIGH mark in ``need_bad`` of the last ``window`` samples, so one spike never
  moves the limit. A cooldown separates decreases and a quiet period after one forbids
  increases, so the limit does not saw up and down.
* A signal that is unavailable (``None``) is neutral for decreases and can never justify
  an increase. With no host signal at all the limit stays at the start value.
* A CRITICAL reading in the latest sample sets ``admit_paused`` for that evaluation only;
  it never rewrites the limit by itself (the sample still counts as over the high mark).
* A gap longer than ``quiet_after_decrease_s`` with no samples forgets everything and
  returns to the start value.

Stdlib only.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

NO_SIGNALS = "start (no signals)"
CEILING = "ceiling"
INDEPENDENT = "independent work"

#: Signal names a controller treats as describing the host. Callers map their own
#: sources onto these names; thresholds for them live in ``Params.thresholds``.
HOST_SIGNALS = ("load_per_core", "memory_pressure", "disk_pressure")


@dataclass(frozen=True)
class Threshold:
    """Marks for one signal, higher meaning worse.

    ``low`` is the healthy mark (at or under it counts as healthy), ``high`` the bad
    mark (over it counts as bad); between them the signal is neutral, which is the
    hysteresis band. ``critical`` (optional) pauses admission.
    """

    low: float
    high: float
    critical: float | None = None


def _default_thresholds() -> dict[str, Threshold]:
    """The shipped marks, kept equal to `[schedule.signals]`'s defaults (`core` reads no
    config; tests/test_adaptive_config.py holds the two together)."""
    return {
        "load_per_core": Threshold(low=0.15, high=0.75),
        "memory_pressure": Threshold(low=0.75, high=0.85, critical=0.95),
        "disk_pressure": Threshold(low=0.85, high=0.90, critical=0.97),
        "reviewer_latency_ratio": Threshold(low=1.5, high=2.0),
        "gate_failure_ratio": Threshold(low=1.5, high=2.0),
    }


@dataclass(frozen=True)
class Sample:
    at: float
    signals: Mapping[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Params:
    start: int = 4
    floor: int = 2
    ceiling: int = 8
    adapt_up_after_s: float = 600
    decrease_factor: float = 0.75
    cooldown_s: float = 300
    quiet_after_decrease_s: float = 1200
    window: int = 4
    need_bad: int = 3
    sample_every_s: float = 60
    thresholds: Mapping[str, Threshold] = field(default_factory=_default_thresholds)
    host_signals: tuple[str, ...] = HOST_SIGNALS

    def evidence(self) -> tuple[int, int]:
        """(window, need_bad) made consistent: one sample must never move the limit, so
        window >= 2 and 2 <= need_bad <= window."""
        window = max(2, int(self.window))
        return window, min(max(2, int(self.need_bad)), window)

    def bounds(self) -> tuple[int, int, int]:
        """(floor, start, ceiling) made consistent: 1 <= floor <= start <= ceiling."""
        ceiling = max(1, int(self.ceiling))
        floor = min(max(1, int(self.floor)), ceiling)
        start = min(max(int(self.start), floor), ceiling)
        return floor, start, ceiling


@dataclass(frozen=True)
class Decision:
    limit: int
    mode: str  # what the replay last did: "start" | "increase" | "decrease"
    limited_by: str
    reason: str
    admit_paused: bool = False


def _ratio(value: float, mark: float) -> float:
    return value / mark if mark > 0 else value - mark


def _classify(sample: Sample, params: Params):
    """(host_available, bad_signal_or_None, healthy) for one sample."""
    sig = sample.signals
    host = any(sig.get(h) is not None for h in params.host_signals)
    worst: tuple[float, str] | None = None
    healthy_host = False
    all_low = True
    for name in sorted(params.thresholds):
        t = params.thresholds[name]
        v = sig.get(name)
        if v is None:
            continue
        if v > t.high:
            key = (_ratio(v, t.high), name)
            if worst is None or key > worst:
                worst = key
        if v > t.low:
            all_low = False
        elif name in params.host_signals:
            healthy_host = True
    healthy = worst is None and all_low and healthy_host
    return host, (worst[1] if worst else None), healthy


def _closest(sample: Sample, params: Params) -> str | None:
    """The signal nearest its high mark among those NOT healthy (over their low mark): a
    signal at or under its low mark limits nothing, however close it is (bug B277cc2591b)."""
    best: tuple[float, str] | None = None
    for name in sorted(params.thresholds):
        v = sample.signals.get(name)
        if v is None or v <= params.thresholds[name].low:
            continue
        key = (_ratio(v, params.thresholds[name].high), name)
        if best is None or key > best:
            best = key
    return best[1] if best else None


def _critical(sample: Sample, params: Params) -> str | None:
    for name in sorted(params.thresholds):
        c = params.thresholds[name].critical
        v = sample.signals.get(name)
        if c is not None and v is not None and v >= c:
            return name
    return None


def _in_flight_at(history: Sequence[tuple[float, int]], times: list[float], t: float) -> int:
    i = bisect.bisect_right(times, t) - 1
    return history[i][1] if i >= 0 else 0


@dataclass
class _Replay:
    """Mutable state of one replay; built fresh by every :func:`fold_limit` call."""

    params: Params
    history: Sequence[tuple[float, int]]
    times: list[float]
    limit: int = 0
    mode: str = "start"
    window: list[tuple[bool, str | None]] = field(default_factory=list)
    accum: float = 0.0
    last_dec: float | None = None
    dec_signal: str | None = None
    last_change: str = ""
    saw_host: bool = False
    prev_ok: bool = False  # previous sample was healthy and outside the quiet period

    def __post_init__(self) -> None:
        self.limit = self.params.bounds()[1]

    def why_not_higher(self, now: float) -> str:
        """With every signal healthy: the growth step, while the limit is binding (it
        rises one per `adapt_up_after_s` of that), else the demand that does not reach it."""
        flying = _in_flight_at(self.history, self.times, now)
        if flying < self.limit:
            return f"demand ({flying} in flight)"
        wait = self.params.adapt_up_after_s - self.accum
        if self.quiet(now) and self.last_dec is not None:
            wait += self.last_dec + self.params.quiet_after_decrease_s - now
        return f"growth step (next in {max(0, round(wait))}s)"

    def quiet(self, at: float) -> bool:
        return self.last_dec is not None and at - self.last_dec < self.params.quiet_after_decrease_s

    def observe(self, s: Sample, dt: float) -> None:
        p = self.params
        host, bad_signal, healthy = _classify(s, p)
        if not host:
            self.prev_ok, self.accum = False, 0.0  # blind: cannot justify an increase
            return  # blind sample: neutral for everything
        self.saw_host = True
        self.window.append((bad_signal is not None, bad_signal))
        window, need_bad = p.evidence()
        del self.window[:-window]
        quiet = self.quiet(s.at)
        ok = healthy and not quiet
        if not ok:
            self.accum = 0.0
        elif self.prev_ok and _in_flight_at(self.history, self.times, s.at) >= self.limit:
            self.accum += dt
        self.prev_ok = ok
        if sum(1 for b, _ in self.window if b) >= need_bad:
            self._maybe_decrease(s.at, bad_signal)
        elif (
            ok
            and self.accum > 0
            and self.accum >= p.adapt_up_after_s
            and self.limit < p.bounds()[2]
        ):
            self.limit += 1
            self.mode, self.accum, self.dec_signal = "increase", 0.0, None
            self.last_change = (
                f"raised to {self.limit}: healthy for {int(p.adapt_up_after_s)}s "
                "with the limit binding"
            )

    def _maybe_decrease(self, at: float, bad_signal: str | None) -> None:
        p = self.params
        cooled = self.last_dec is None or at - self.last_dec >= p.cooldown_s
        new = max(p.bounds()[0], math.floor(self.limit * p.decrease_factor))
        if not cooled or new >= self.limit:
            return
        names = [n for b, n in self.window if b and n]
        signal = bad_signal or (names[-1] if names else None)
        self.limit, self.mode, self.last_dec, self.dec_signal = new, "decrease", at, signal
        self.last_change = (
            f"lowered to {new}: {signal} over its high mark in {p.evidence()[1]} of "
            f"the last {p.evidence()[0]} samples"
        )
        self.window, self.accum, self.prev_ok = [], 0.0, False


def fold_limit(
    samples: Sequence[Sample],
    params: Params,
    in_flight_history: Sequence[tuple[float, int]],
    now: float,
) -> Decision:
    """Replay ``samples`` from the start value and return the limit in force at ``now``.

    ``in_flight_history`` is ``(at, in_flight)`` points, the count holding from each
    ``at`` until the next; it tells the controller whether the limit was binding.
    """
    start, ceiling = params.bounds()[1], params.bounds()[2]
    ring = sorted((s for s in samples if s.at <= now), key=lambda s: s.at)
    history = sorted(in_flight_history, key=lambda h: h[0])
    times = [h[0] for h in history]
    st = _Replay(params, history, times)
    prev: float | None = None
    for s in ring:
        gap = None if prev is None else s.at - prev
        if gap is not None and gap > params.quiet_after_decrease_s:
            st = _Replay(params, history, times)
        dt = 0.0 if gap is None else min(gap, 2 * params.sample_every_s)
        prev = s.at
        st.observe(s, dt)

    if not ring:
        return Decision(start, "start", NO_SIGNALS, "no samples yet", False)
    if now - ring[-1].at > params.quiet_after_decrease_s:
        why = "samples are stale; back at the start value"
        return Decision(start, "start", NO_SIGNALS, why, False)
    if not st.saw_host:
        return Decision(start, "start", NO_SIGNALS, "no host signal available", False)

    paused_by = _critical(ring[-1], params)
    if st.limit >= ceiling:
        limited_by = CEILING
    elif st.dec_signal and ring[-1].signals.get(st.dec_signal) is not None:
        # The decrease's signal, while it still reads; once it is null it holds nothing,
        # and naming it hid what really did (B767745dee6).
        limited_by = st.dec_signal
    else:
        # growth or demand only when the latest sample SHOWS health: a blind one says so
        healthy = _classify(ring[-1], params)[2]
        limited_by = _closest(ring[-1], params) or (
            st.why_not_higher(now) if healthy else NO_SIGNALS
        )
    reason = st.last_change or f"holding at {st.limit}"
    if paused_by:
        reason = f"{paused_by} is critical: admission paused; {reason}"
    return Decision(st.limit, st.mode, limited_by, reason, paused_by is not None)


def decide_admission(
    decision: Decision,
    in_flight: int,
    independent_ready: int,
    ceiling: int | None = None,
) -> int:
    """Target number in flight: no more than the limit, and no more than there is work
    that can run in parallel with what already runs. Clamped to ``[1, ceiling]``.
    ``decision.admit_paused`` is the caller's gate on starting anything new."""
    target = min(decision.limit, max(0, in_flight) + max(0, independent_ready))
    top = decision.limit if ceiling is None else min(decision.limit, ceiling)
    return max(1, min(target, max(1, top)))
