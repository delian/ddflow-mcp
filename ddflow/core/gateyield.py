"""Gate yield, co-failure and same-input analysis as pure detectors (WP-gate-yield).

Per gate, from ``facts.collect``: the first-run pass rate, the share of items that ever
failed it, and the elapsed seconds it cost per confirmed finding. Across gates: the
co-failure phi between two review gates (their verdicts on the same items), the groups of
gates that ran on the identical tree, and rework loops (an item completed, then a bug was
filed against it later).

Pure functions: no event is appended, no process started, no second collector. The
P-observe detector registry (PM-observe-bridge) calls these; they never register
themselves there. A figure under ``[metrics].min_samples`` is ``None``, never zero.

Research: R-path-merge (gate processes 27% of claim-to-merge; overlap saves 110-150h/30d).
"""

from __future__ import annotations

from collections import defaultdict
from math import sqrt

from ddflow.services.metrics.facts import Facts


def _pct(num: int, den: int, minimum: int) -> float | None:
    """``num / den`` when ``den`` reached the sample floor, else ``None`` (never 0)."""
    return num / den if den >= max(minimum, 1) else None


def _first_verdicts(facts: Facts) -> dict[tuple[str, str], str]:
    """(item, gate) -> the kind of its FIRST recorded verdict (facts are time-ordered)."""
    first: dict[tuple[str, str], str] = {}
    for o in facts.outcomes:
        key = (o.item, o.gate)
        if key not in first:
            first[key] = o.kind
    return first


def gate_rows(facts: Facts, minimum: int = 3) -> list[dict]:
    """Per gate: items, first-run pass rate, ever-failed share, cost per confirmed finding."""
    first = _first_verdicts(facts)
    ever_failed: dict[str, set[str]] = defaultdict(set)
    elapsed: dict[str, float] = defaultdict(float)
    for o in facts.outcomes:
        if o.kind == "failed":
            ever_failed[o.gate].add(o.item)
        if o.elapsed_s:
            elapsed[o.gate] += o.elapsed_s
    confirmed: dict[str, int] = defaultdict(int)
    for f in facts.findings:
        if f.verdict == "confirmed":
            confirmed[f.gate] += 1

    rows: list[dict] = []
    for gate in sorted({o.gate for o in facts.outcomes}):
        firsts: dict[tuple[str, str], str] = {k: v for k, v in first.items() if k[1] == gate}
        passed = sum(1 for v in firsts.values() if v == "passed")
        gone = ever_failed[gate]
        rows.append(
            {
                "gate": gate,
                "items": len(firsts),
                "first_run_pass_rate": _pct(passed, len(firsts), minimum),
                "ever_failed_share": _pct(len(gone), len(firsts), minimum),
                "cost_per_confirmed_finding_s": (
                    elapsed[gate] / confirmed[gate] if confirmed[gate] else None
                ),
            }
        )
    return rows


def _phi(n11: int, n10: int, n01: int, n00: int) -> float | None:
    """Phi coefficient of the 2x2 table (how much the two gates agree on pass/fail)."""
    den = sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    return (n11 * n00 - n10 * n01) / den if den else None


def co_failure(facts: Facts, a: str, b: str, minimum: int = 3) -> dict[str, int | float | None]:
    """The pass/fail contingency and phi of two gates over the items that ran BOTH."""
    by_item: dict[str, dict[str, str]] = defaultdict(dict)
    for o in facts.outcomes:
        by_item[o.item][o.gate] = o.kind
    n11 = n10 = n01 = n00 = 0
    for verdicts in by_item.values():
        if a not in verdicts or b not in verdicts:
            continue
        fa, fb = verdicts[a] == "failed", verdicts[b] == "failed"
        n11 += fa and fb
        n10 += fa and not fb
        n01 += not fa and fb
        n00 += not fa and not fb
    n = n11 + n10 + n01 + n00
    return {
        "a": a,
        "b": b,
        "both_failed": n11,
        "a_only": n10,
        "b_only": n01,
        "neither": n00,
        "phi": _phi(n11, n10, n01, n00) if n >= max(minimum, 1) else None,
    }


def same_tree_groups(facts: Facts) -> list[dict]:
    """Groups of gates whose verdicts name the identical tree (a merged review round)."""
    by_tree: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for o in facts.outcomes:
        if o.tree_sha:
            by_tree[o.tree_sha].add((o.item, o.gate))
    return [
        {"tree_sha": sha, "runs": sorted(g for _, g in pairs), "items": len({i for i, _ in pairs})}
        for sha, pairs in sorted(by_tree.items())
        if len(pairs) > 1  # a group is at least two gates on one tree
    ]


def rework_loops(facts: Facts) -> list[dict]:
    """A completed item that a bug was later filed against (the rework the report shows)."""
    completed_at: dict[str, float] = {}
    for o in facts.outcomes:
        if o.at >= completed_at.get(o.item, 0.0):
            completed_at[o.item] = o.at
    loops: list[dict] = []
    for item, at in sorted(facts.bugs, key=lambda x: x[1]):
        if item in completed_at and at > completed_at[item]:
            loops.append({"item": item, "completed_at": completed_at[item], "bug_at": at})
    return loops


def build(facts: Facts, minimum: int = 3) -> dict:
    """The gate-yield scoreboard as one plain structure (the report/registry reads it)."""
    return {
        "gates": gate_rows(facts, minimum),
        "co_failure": co_failure(facts, "rubber_duck", "critic", minimum),
        "same_tree": same_tree_groups(facts),
        "rework": rework_loops(facts),
    }


__all__ = ["build", "co_failure", "gate_rows", "rework_loops", "same_tree_groups"]
