"""Merge-window overlap and batch eligibility, derived from the lease log alone.

Two items are in the same *merge window* when their lease intervals overlap in time; they
are *path-disjoint* when no glob of one overlaps a glob of the other (``core.globs.overlap``,
the same test the lease engine uses, so there is no second definition of disjoint). An item is
batch-eligible when it is small enough and has a time-overlapping, path-disjoint partner that
is small enough too. An item whose lease named no glob is never called disjoint (unknown is
not disjoint), and a lease still held has no end, so it is left out and named in ``skipped``.
"""

from __future__ import annotations

from ...core import globs as G
from .providers import Context
from .stats import rate

#: Size ceiling for "small enough to batch" (the D-BG-Q2 default max_lines; the ``[batch]``
#: config section of BG-policy replaces it). A figure, not a knob of the measurement.
ELIGIBLE_MAX_LINES = 800
#: Cap on time-overlapping pairs examined, so a huge log cannot stall the view (a visible stop).
MAX_PAIRS = 200_000


def _windows(ctx: Context) -> tuple[dict[str, tuple[float, float, tuple[str, ...]]], int]:
    """item -> (first lease start, last lease end, union of lease globs); and how many items
    were left out because a lease was still held."""
    out: dict[str, tuple[float, float, tuple[str, ...]]] = {}
    open_items: set[str] = set()
    unknown: set[str] = set()
    for lease in ctx.facts.leases:
        if not ctx.in_scope(lease.item, lease.start):
            continue
        if lease.end is None:
            open_items.add(lease.item)
            continue
        s, e, g = out.get(lease.item, (lease.start, lease.end, ()))
        out[lease.item] = (
            min(s, lease.start),
            max(e, lease.end),
            tuple(sorted(set(g) | set(lease.globs))),
        )
        if not lease.globs:
            unknown.add(lease.item)  # one glob-less lease: the item's paths are not known
    for item in unknown:
        s, e, _g = out[item]
        out[item] = (s, e, ())
    for item in open_items:
        out.pop(item, None)
    return out, len(open_items)


def _disjoint(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if not a or not b:
        return False
    return not any(G.overlap(x, y) for x in a for y in b)


def build(ctx: Context, size: dict[str, int | None]) -> dict:
    """The overlap table as plain data."""
    minimum = int(ctx.cfg.metrics.min_samples)
    win, held = _windows(ctx)
    order = sorted(win, key=lambda i: win[i][0])
    pairs = disjoint = 0
    eligible: set[str] = set()
    truncated = False
    small = {i for i in win if size.get(i) and (size[i] or 0) <= ELIGIBLE_MAX_LINES}
    for n, a in enumerate(order):
        a_end = win[a][1]
        for m in range(n + 1, len(order)):
            b = order[m]
            if win[b][0] >= a_end:
                break  # sorted by start: nothing later overlaps a
            if pairs >= MAX_PAIRS:
                truncated = True
                break
            pairs += 1
            if _disjoint(win[a][2], win[b][2]):
                disjoint += 1
                if a in small and b in small:
                    eligible.update((a, b))
        if truncated:
            break
    return {
        "items": len(win),
        "pairs_overlapping": pairs,
        "pairs_path_disjoint": disjoint,
        "eligible_items": len(eligible),
        "eligible_share": rate(len(eligible), len(win), minimum),
        "max_lines": ELIGIBLE_MAX_LINES,
        "skipped": {"lease_still_held": held} if held else {},
        "truncated": truncated,
    }


def text(ov: dict) -> str:
    share = "n/a" if ov["eligible_share"] is None else f"{ov['eligible_share'] * 100:.0f}%"
    out = (
        f"merge windows: {ov['items']} items, {ov['pairs_overlapping']} overlapping pairs, "
        f"{ov['pairs_path_disjoint']} path-disjoint; batch-eligible "
        f"({ov['max_lines']} lines or fewer, a disjoint partner): "
        f"{ov['eligible_items']} = {share}"
    )
    if ov["truncated"]:
        out += f" (stopped at {MAX_PAIRS} pairs)"
    return out
