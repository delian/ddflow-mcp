"""The session and cost governor's decision (D-quotas-governor): a pure function.

``decide`` takes the meters (quota fractions per subject, context fraction, load signals),
the task (role, requested tier and effort, mechanical or not), the floors and the LAST
decision, and returns an action, the tier and effort caps in force, and the reason. It
reads nothing and keeps no state: ``replay`` rebuilds the answer from a sample ring, the
way ``core.flowcontrol.fold_limit`` does, and reuses its ``Sample`` and ``Threshold``
(no second implementation).

Ladders step on marks (a reading AT a mark steps up). A step up is immediate; a step down
needs the reading to fall ``hysteresis`` below the mark it left AND ``cooldown_s`` since
the ladder last moved, so a reading hovering at a mark does not flap. A reading that is
unavailable (``None``) holds the ladder where it is; it is never read as zero. A signal
the caller leaves out of ``load`` is simply not measured, not unavailable: the caller names
what it samples (``None`` is how it says a sampled signal could not be read).

* quota ladder (marks 0.4/0.6/0.8): 1 caps new work at the balanced tier and medium
  effort; 2 caps mechanical work at fast and other work at low effort; 3 pauses and asks.
* context ladder (marks 0.6/0.75/0.85/0.95): checkpoint, handoff, compact, emergency.
* load ladder: flowcontrol's own marks (over high = 1, at critical = 2), no second reader.

Floors win over caps: a reviewer never drops below its floor tier and a task tagged deep is
never downshifted. Stdlib only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .flowcontrol import Sample, Threshold, _default_thresholds

TIERS = ("fast", "balanced", "deep")
EFFORTS = ("low", "medium", "high")

CONTINUE = "continue"
DOWNSHIFT = "downshift"
CHECKPOINT = "checkpoint"
HANDOFF = "handoff"
COMPACT = "compact"
EMERGENCY = "emergency_checkpoint"
PAUSE_AND_ASK = "pause-and-ask"
ACTIONS = (CONTINUE, DOWNSHIFT, CHECKPOINT, HANDOFF, COMPACT, EMERGENCY, PAUSE_AND_ASK)

#: Ladder levels that mean something on their own.
CAP_FIRST, CAP_SECOND, QUOTA_PAUSE, CONTEXT_EMERGENCY = 1, 2, 3, 4
_CONTEXT_ACTIONS = ("", CHECKPOINT, HANDOFF, COMPACT, EMERGENCY)


def _rank(order: tuple[str, ...], name: str, fallback: int) -> int:
    return order.index(name) if name in order else fallback


@dataclass(frozen=True)
class Params:
    quota_marks: tuple[float, float, float] = (0.4, 0.6, 0.8)
    context_marks: tuple[float, float, float, float] = (0.6, 0.75, 0.85, 0.95)
    hysteresis: float = 0.05
    cooldown_s: float = 300
    tier_cap_1: str = "balanced"
    tier_cap_2: str = "fast"  # for mechanical work
    tier_cap_2_other: str = "balanced"  # for the rest
    effort_cap_1: str = "medium"
    effort_cap_2: str = "low"
    reviewer_floor_tier: str = "balanced"
    deep_floor: bool = True
    effort_floor: str = "low"
    load_governs: bool = True
    load_thresholds: Mapping[str, Threshold] = field(default_factory=_default_thresholds)

    def quota(self) -> tuple[float, ...]:
        return tuple(sorted(self.quota_marks))

    def context(self) -> tuple[float, ...]:
        return tuple(sorted(self.context_marks))


@dataclass(frozen=True)
class Request:
    """What the task asked for and what protects it."""

    role: str = "worker"  # "worker" | "reviewer"
    tier: str = "balanced"
    effort: str = "high"
    mechanical: bool = False


@dataclass(frozen=True)
class Ladder:
    """Where one ladder stands and since when (the cooldown's clock)."""

    level: int = 0
    at: float = 0.0


@dataclass(frozen=True)
class Decision:
    action: str
    tier: str
    effort: str
    reason: str
    quota: Ladder = Ladder()
    context: Ladder = Ladder()
    load: Ladder = Ladder()
    tier_cap: str = ""  # the cap in force before floors ("" = none)
    effort_cap: str = ""


def step(
    value: float | None,
    marks: Sequence[float],
    last: Ladder | None,
    now: float,
    hysteresis: float,
    cooldown_s: float,
    blind: bool = False,
) -> Ladder:
    """The ladder's level for ``value``: up at once on reaching a mark; down only when the
    value is under the mark it left minus ``hysteresis`` and the ladder has been still for
    ``cooldown_s``. ``None`` holds, and so does ``blind`` (some reading that could still be
    high is unavailable) against a step down. Monotone in ``value`` for a fixed ``last``."""
    prev = last or Ladder(0, now)
    if value is None:
        return prev
    level = prev.level
    while level < len(marks) and value >= marks[level]:
        level += 1
    if level > prev.level:
        return Ladder(level, now)
    down = prev.level
    while down > 0 and value < marks[down - 1] - hysteresis:
        down -= 1
    if down < prev.level and not blind and now - prev.at >= cooldown_s:
        return Ladder(down, now)
    return prev


def _signal_level(v: float, t: Threshold, held: bool) -> int:
    """One load signal's level: critical 2, over its high mark (or still over the low mark
    while ``held`` at 1 or more: the hysteresis band) 1, else 0."""
    if t.critical is not None and v >= t.critical:
        return 2
    return 1 if v > t.high or (held and v > t.low) else 0


def _load_step(
    load: Mapping[str, float | None], p: Params, last: Ladder | None, now: float
) -> Ladder:
    prev = last or Ladder(0, now)
    cand = max(
        (
            _signal_level(v, t, prev.level >= 1)
            for name, t in p.load_thresholds.items()
            if (v := load.get(name)) is not None
        ),
        default=0,
    )
    seen = [n for n in p.load_thresholds if n in load]
    blind = any(load[n] is None for n in seen)
    if not seen or all(load[n] is None for n in seen):
        return prev  # blind: holds, never zero
    if cand > prev.level:
        return Ladder(cand, now)
    if cand < prev.level and not blind and now - prev.at >= p.cooldown_s:
        return Ladder(cand, now)
    return prev


def _floors(req: Request, p: Params) -> tuple[int, int]:
    """(tier rank, effort rank) the result may not drop under."""
    tier = 0
    if req.role == "reviewer":
        tier = _rank(TIERS, p.reviewer_floor_tier, 1)
    effort = _rank(EFFORTS, p.effort_floor, 0)
    if p.deep_floor and req.tier == "deep":
        tier = _rank(TIERS, "deep", 2)
        effort = max(effort, _rank(EFFORTS, req.effort, 2))  # never downshifted, in effort too
    return tier, effort


def decide(
    quota: Mapping[str, float | None],
    context: float | None,
    load: Mapping[str, float | None],
    request: Request,
    params: Params,
    last: Decision | None,
    now: float,
) -> Decision:
    """The governor's answer. ``quota`` holds one fraction per limited subject (an
    unlimited subject is simply absent); the worst one rules."""
    p = params
    readings = [v for v in quota.values() if v is not None]
    q = step(
        max(readings) if readings else None,
        p.quota(),
        last.quota if last else None,
        now,
        p.hysteresis,
        p.cooldown_s,
        blind=any(v is None for v in quota.values()),
    )
    c = step(context, p.context(), last.context if last else None, now, p.hysteresis, p.cooldown_s)
    ld = _load_step(load, p, last.load if last else None, now) if p.load_governs else Ladder(0, now)

    tier, eff, tier_cap, effort_cap = _apply_caps(
        min(CAP_SECOND, max(q.level, ld.level)), request, p
    )
    action = _action(q.level, c.level, tier, eff, request)
    reasons = _reasons(q, c, ld, p)
    if action == DOWNSHIFT:
        reasons.append(
            f"tier {request.tier}->{TIERS[tier]}, effort {request.effort}->{EFFORTS[eff]}"
        )
    return Decision(
        action,
        TIERS[tier],
        EFFORTS[eff],
        "; ".join(reasons) or "within every mark",
        q,
        c,
        ld,
        tier_cap,
        effort_cap,
    )


def _apply_caps(cap_level: int, req: Request, p: Params) -> tuple[int, int, str, str]:
    """(tier rank, effort rank, tier cap, effort cap) after the ladder's caps and floors."""
    tier, eff = _rank(TIERS, req.tier, 1), _rank(EFFORTS, req.effort, 2)
    tier_cap = effort_cap = ""
    if cap_level >= CAP_FIRST:
        tier_cap, effort_cap = p.tier_cap_1, p.effort_cap_1
    if cap_level >= CAP_SECOND:
        tier_cap = p.tier_cap_2 if req.mechanical else p.tier_cap_2_other
        effort_cap = p.effort_cap_2
    if tier_cap:
        tier = min(tier, _rank(TIERS, tier_cap, 0))
        eff = min(eff, _rank(EFFORTS, effort_cap, 0))
    floor_t, floor_e = _floors(req, p)
    return max(tier, floor_t), max(eff, floor_e), tier_cap, effort_cap


def _action(quota_level: int, context_level: int, tier: int, eff: int, req: Request) -> str:
    if context_level >= CONTEXT_EMERGENCY:
        return EMERGENCY
    if quota_level >= QUOTA_PAUSE:
        return PAUSE_AND_ASK
    if context_level >= 1:
        return _CONTEXT_ACTIONS[context_level]
    if tier < _rank(TIERS, req.tier, 1) or eff < _rank(EFFORTS, req.effort, 2):
        return DOWNSHIFT
    return CONTINUE


def _reasons(q: Ladder, c: Ladder, ld: Ladder, p: Params) -> list[str]:
    out = []
    if q.level:
        out.append(f"quota ladder at {q.level} of {len(p.quota())}")
    if c.level:
        out.append(f"context ladder at {c.level} of {len(p.context())}")
    if ld.level:
        out.append(f"load ladder at {ld.level}")
    return out


def replay(
    samples: Sequence[Sample],
    request: Request,
    params: Params,
    now: float | None = None,
) -> Decision | None:
    """Re-judge a run from its samples (oldest first). A sample's signals are
    ``quota:<subject>`` fractions, ``context`` and the load signals by flowcontrol's names.
    Returns None for no samples."""
    last: Decision | None = None
    for s in sorted(samples, key=lambda s: s.at):
        if now is not None and s.at > now:
            continue
        quota = {k[6:]: v for k, v in s.signals.items() if k.startswith("quota:")}
        load = {k: v for k, v in s.signals.items() if k in params.load_thresholds}
        last = decide(quota, s.signals.get("context"), load, request, params, last, s.at)
    return last


def params_from_config(cfg) -> Params:
    """``Params`` from the ``[govern]`` section of a loaded config."""
    g = cfg.govern
    return Params(
        quota_marks=(g.quota_mark_1, g.quota_mark_2, g.quota_mark_3),
        context_marks=(g.context_mark_1, g.context_mark_2, g.context_mark_3, g.context_mark_4),
        hysteresis=g.hysteresis,
        cooldown_s=g.cooldown_s,
        tier_cap_1=g.tier_cap_1,
        tier_cap_2=g.tier_cap_2,
        tier_cap_2_other=g.tier_cap_2_other,
        effort_cap_1=g.effort_cap_1,
        effort_cap_2=g.effort_cap_2,
        reviewer_floor_tier=g.reviewer_floor_tier,
        deep_floor=g.deep_floor,
        effort_floor=g.effort_floor,
        load_governs=g.load_governs,
    )
