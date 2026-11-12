"""The orchestration capability report (ORC-capability-fields).

An agent's orchestration capabilities -- what it exposes and what patterns it offers an
orchestrator -- are data on the descriptor v2, in ``[capabilities]``. ``services.harnessv2``
owns the record and its closed vocabularies (``model_visibility``, ``cost_exposed``,
``subagent_model_control``, ``patterns``, each with a ``*_source`` naming the file and key
that set it), and the descriptor loader refuses a verified value without its source. This
module grades the line a descriptor declares: which capabilities are VERIFIED (a ``*_source``
is cited) and which stay unknown -- never inferred.
"""

from __future__ import annotations

from collections.abc import Sequence

from .. import harnessreg as HR
from .. import harnessv2 as HV

#: The single-valued, enum-graded capability fields (each pairs with a ``*_source``).
VALUE_FIELDS = ("model_visibility", "cost_exposed", "subagent_model_control")


def record(h: HR.Harness) -> HV.Capabilities:
    """``h``'s ``[capabilities]``, or an all-unknown default when it declares none. A section
    left out of a descriptor is NOT VERIFIED (unknown), never defaulted to a fact."""
    cap = getattr(h, "capabilities", None)
    return cap if cap is not None and not cap.none else HV.Capabilities()


def verified(cap: HV.Capabilities) -> frozenset[str]:
    """The capability fields ``cap`` declares VERIFIED: a scalar whose value is not ``unknown``
    (so, by ``harnessv2``, it cites a source) plus ``patterns`` when non-empty."""
    out: set[str] = {n for n in VALUE_FIELDS if getattr(cap, n) not in ("", "unknown")}
    if cap.patterns:
        out.add("patterns")
    return frozenset(out)


def report(hs: Sequence[HR.Harness] | None = None) -> dict[str, dict]:
    """Per-agent orchestration capability grade over every descriptor (default ``HR.load()``).
    ``verified`` names the fields this module grades as sourced, not guessed."""
    hs = list(HR.load() if hs is None else hs)
    out: dict[str, dict] = {}
    for h in hs:
        cap = record(h)
        out[h.id] = {
            "model_visibility": cap.model_visibility,
            "cost_exposed": cap.cost_exposed,
            "subagent_model_control": cap.subagent_model_control,
            "patterns": cap.patterns,
            "verified": sorted(verified(cap)),
        }
    return out
