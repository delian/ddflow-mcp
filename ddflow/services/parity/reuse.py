"""Reuse measure and ratchet for agent-specific Python (PAR-data-first).

A new agent is a descriptor plus fixtures; Python exists only for a real gap, and a gap
is a hook normalizer or emitter used by exactly one descriptor. Everything else a new
agent needs is descriptor DATA (``ddflow/harnesses/<id>.toml``), the driver delta note
(``templates/drivers/deltas/<id>.md``) and its recorded fixtures; every shared
normalizer/emitter is reused, not re-written.

DRV-conformance proxied this as an emitter-only rate (an agent whose emit variant is used
by no other descriptor is bespoke; one with no command hooks needs none); this module is
the first-class report. It grades a descriptor bespoke on two axes, each a piece of
Python that only one descriptor uses:

* ``emitter``    -- the ``[hooks] emitter`` emit variant. A variant in
  ``services/engines/emit`` (re-exported from ``services/hookio``) used by no other
  descriptor is a per-agent module;
* ``normalizer``  -- the ``[hooks.fields]`` stdin field map (which facts the hook reads
  and where). The map is descriptor DATA resolved by ``hookio.fieldmap``, but a map no
  other descriptor declares is one more per-agent thing to keep.

An agent needs no per-agent Python when neither of its two axes is bespoke; an agent with
no command/plugin hook needs none at all. ``measure()`` returns the report; ``unrecorded()``
is the ratchet -- a descriptor may not add bespoke Python unless the gap is recorded
(with its reason) in its delta note, which the committed ``recorded`` set lists. This is
the one home for the measure; the test suite's ``harness_conformance.report.zero_python``
is the emitter-only proxy it generalizes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from .. import harnessreg as HR

#: A hook normalizer or emitter used by fewer descriptors than this is bespoke Python.
SHARE_MIN = 2

#: The normalizer axis; the emit-variant axis names the "agent-specific module".
NORMALIZER = "normalizer"
EMITTER = "emitter"


def _normalizer_key(fields: dict[str, tuple[str, ...]] | None) -> tuple:
    """The field map's hashable signature: ``()`` for the shared default dialect (an absent
    or empty ``[hooks.fields]`` resolves to ``hookio``'s Claude-shaped fallback), else the
    declared (fact, paths) map. The map is DATA; two descriptors that declare the same map
    reuse one normalizer."""
    if not fields:
        return ()
    return tuple(sorted((name, tuple(paths)) for name, paths in fields.items()))


def _hooked(hs: Sequence[HR.Harness]) -> list[HR.Harness]:
    """The agents that use a hook normalizer and emitter at all. An agent whose hooks
    declare no emit variant (``h.hooks.emitter == ""``, e.g. ``style = "none"`` or no
    hooks section) has neither axis and needs no Python."""
    return [h for h in hs if h.hooks is not None and bool(h.hooks.emitter)]


def axes(h: HR.Harness) -> tuple[tuple, str] | None:
    """``(normalizer_key, emitter)`` for ``h``, or ``None`` when it uses neither."""
    if h.hooks is None or not h.hooks.emitter:
        return None
    return (_normalizer_key(h.hooks.fields), h.hooks.emitter)


def usage(hs: Sequence[HR.Harness]) -> tuple[Counter, Counter]:
    """``(normalizer_use, emitter_use)`` over the hooked agents: each key/count. A key used
    by fewer than :data:`SHARE_MIN` agents names the bespoke axis that only that agent (and
    at most that agent's) uses."""
    norm = Counter()
    emit = Counter()
    for h in _hooked(hs):
        key, name = axes(h)
        norm[key] += 1
        emit[name] += 1
    return norm, emit


def bespoke(hs: Sequence[HR.Harness] | None = None) -> dict[str, frozenset[str]]:
    """Agent id -> the axes (``normalizer``, ``emitter``) only that agent uses. An agent in
    the result has per-agent Python; one absent from it is pure data (or hookless)."""
    hs = list(HR.load() if hs is None else hs)
    norm, emit = usage(hs)
    out: dict[str, frozenset[str]] = {}
    for h in _hooked(hs):
        key, name = axes(h)
        parts: set[str] = set()
        if norm[key] < SHARE_MIN:
            parts.add(NORMALIZER)
        if emit[name] < SHARE_MIN:
            parts.add(EMITTER)
        if parts:
            out[h.id] = frozenset(parts)
    return out


def measure(hs: Sequence[HR.Harness] | None = None) -> dict:
    """The first-class report over every descriptor (default ``HR.load()``)."""
    hs = list(HR.load() if hs is None else hs)
    _, emit = usage(hs)
    by_agent = bespoke(hs)
    return {
        "agents": len(hs),
        "hooked": len(_hooked(hs)),
        "zero_python": len(hs) - len(by_agent),
        "rate": round((len(hs) - len(by_agent)) / len(hs), 3) if hs else 0.0,
        "bespoke": sorted(by_agent),
        "by_agent": by_agent,
        "normalizer_bespoke": sorted(a for a, p in by_agent.items() if NORMALIZER in p),
        "emitter_bespoke": sorted(a for a, p in by_agent.items() if EMITTER in p),
        #: each emit variant (the agent-specific module) and how many descriptors use it;
        #: one at or below SHARE_MIN used by a SORTED single agent is a bespoke module.
        "emitter_variants": dict(emit),
    }


def unrecorded(hs: Sequence[HR.Harness] | None = None, recorded: Sequence[str] = ()) -> list[str]:
    """The bespoke agents NOT in ``recorded``: a descriptor that added per-agent Python
    without recording the gap -- and its reason -- in its delta note. ``recorded`` is the
    committed set those notes list; the ratchet fails while this is non-empty and, once a
    gap closes (the piece is now shared), the stale ``recorded`` entry must leave the set."""
    recorded_set = set(recorded)
    return sorted(a for a in measure(hs)["bespoke"] if a not in recorded_set)
