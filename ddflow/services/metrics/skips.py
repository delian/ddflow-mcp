"""The gate-skip audit: where the process is routed around, and by whom.

``gate skip`` is the only way past a gate, so its rate per gate, the reasons given, the
skips that no later pass made good and the agents who skip are the audit. Pipeline
requirements register their own rows (a MUST requirement skipped) through the provider
hook, which the same minimum-sample rule covers.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .providers import Context
from .stats import rate


def _gate_row(gate: str, outs: list, passes: dict, minimum: int, top: int) -> dict | None:
    """One gate's skip row, or None for a gate nobody skipped."""
    skipped = [o for o in outs if o.kind == "skipped"]
    if not skipped:
        return None
    passed = sum(o.kind == "passed" for o in outs)
    orphan = [o for o in skipped if not any(t > o.at for t in passes[(o.item, gate)])]
    reasons = Counter(" ".join(o.reason.split())[:100] or "(no reason)" for o in skipped)
    return {
        "gate": gate,
        "skipped": len(skipped),
        "passed": passed,
        "skip_rate": rate(len(skipped), len(skipped) + passed, minimum),
        "never_passed_after": len(orphan),
        "reasons": [{"reason": r, "n": n} for r, n in reasons.most_common(top)],
    }


def audit(ctx: Context) -> dict:
    """Skips per gate with reasons, skips never followed by a pass, and skips by agent."""
    minimum = int(ctx.cfg.metrics.min_samples)
    top = int(ctx.cfg.metrics.top)
    scoped = [o for o in ctx.facts.outcomes if ctx.in_scope(o.item, o.at)]
    by_gate: dict[str, list] = defaultdict(list)
    for o in scoped:
        by_gate[o.gate].append(o)
    passes: dict[tuple[str, str], list[float]] = defaultdict(list)
    for o in ctx.facts.outcomes:
        if o.kind == "passed":
            passes[(o.item, o.gate)].append(o.at)
    gates = [
        row
        for gate, outs in sorted(by_gate.items())
        if (row := _gate_row(gate, outs, passes, minimum, top))
    ]
    gates.sort(key=lambda g: -g["skipped"])
    agents = Counter(o.by or "(unknown)" for o in scoped if o.kind == "skipped")
    return {
        "gates": gates,
        "by_agent": [{"agent": a, "skipped": n} for a, n in agents.most_common(top)],
        "total_skipped": sum(agents.values()),
    }
