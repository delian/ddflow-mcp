"""The keep-vs-delta classification behind `[locks].review_reuse` (LK-review-reuse).

A review gate is keyed to the tree it covered, so a merge of main (or a rebase) marks every
recorded review stale. `refine_stale_notes` has long narrowed that to the item's own paths by
comparing the round's `reviewed_paths` against the tree; this module makes the reuse decision
explicit and knob-gated.

`off` (default, until measured) makes a moved tree invalidate the review: the stale note
stays and the gate is re-reviewed. `disjoint` classifies per gate:

* the item's OWN change is identified by `git patch-id --stable` of its base..tip diff
  restricted to the item's changed paths, with `reviewed_paths` as the witness that it is
  unchanged since the round;
* ``keep`` -- the item's own change is unchanged (`reviewed_paths` still matches) and the
  files the tree move brought in are DISJOINT from the files the reviewer saw, so the review
  is reused and the reason recorded;
* ``delta`` -- the item's change or a merge OVERLAPs a reviewed file, so only the overlapping
  files are re-reviewed;
* any doubt (git cannot name the item's own change, the tree or the changed set is unreadable)
  is ``stale``: reuse is never a guess, it fails closed.

Measured by the wrong-keep rate: a kept review followed by a finding on the same files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .gates.reviewed_paths import ignorable
from .handoff.refs import patch_id as _patch_id
from ..infra import worktree as W

KEEP = "keep"
DELTA = "delta"
STALE = "stale"


@dataclass(frozen=True)
class Verdict:
    """The reuse decision for one review gate's stale note."""

    verdict: str  # keep | delta | stale
    why: str
    overlapping: tuple[str, ...] = ()


def own_patch_id(repo: Path, base: str, tip: str, paths: Sequence[str]) -> str:
    """`git patch-id --stable` of the item's OWN change -- its ``base..tip`` diff restricted
    to ``paths`` (the item's changed files, so another item's commits and main's drift are
    never part of the identity) -- or "" when git cannot say. Empty is not "unchanged": a
    caller that needs the identity reads "" as doubt."""

    if not paths or not base or not tip:
        return ""
    diff = W.git(repo, "diff", "--no-color", f"{base}...{tip}", "--", *paths)
    if not diff.ok:
        return ""
    return _patch_id(repo, diff.out)


def classify(
    recorded: dict[str, str] | None,
    changed: Sequence[str] | None,
    entries,
    own_id: str,
    ignore: Sequence[str] = (),
    docs: Sequence[str] = (),
) -> Verdict:
    """Keep, delta or stale for a round whose `reviewed_paths` is ``recorded``.

    ``entries`` is the current tree ({path: (mode, oid)}) the round is compared against, or
    None when it cannot be read. ``own_id`` is `own_patch_id` of the item's own change ("" =
    cannot be told). Never reuses on doubt: a missing `own_id`, an unreadable tree, an empty
    `recorded` or an unreadable changed set all return `stale` so the review is re-reviewed
    whole. A moved path that is not new work for triage (a reviewed doc matching ``docs``, a
    test matching ``ignore``) is exempt here exactly as in `refine_stale_notes`."""
    if not isinstance(recorded, dict) or not recorded:
        return Verdict(STALE, "the round recorded no reviewed files, so its scope cannot be re-checked")
    if entries is None:
        return Verdict(STALE, "the tree being completed could not be read")
    if not own_id:
        return Verdict(
            STALE,
            "git could not identify the item's own change (git patch-id --stable), "
            "so the review cannot be reused",
        )
    if changed is None:
        return Verdict(STALE, "the item's changed set could not be read, so the review cannot be reused")
    seen = set(recorded)
    gone = {p for p in (seen | set(changed)) if p not in entries}
    # A file the item changes now that the round never reviewed is work the round did not
    # cover: its own change grew since the round, so the review cannot be narrowed to an
    # overlap -- it is stale, not a delta of the covered files.
    added = sorted(p for p in set(changed) - seen if not ignorable(p, recorded, ignore, docs, gone))
    if added:
        shown = ", ".join(added[:8]) + (f" (+{len(added) - 8} more)" if len(added) > 8 else "")  # noqa: PLR2004
        return Verdict(STALE, f"the item now changes file(s) the review never saw: {shown}")
    overlap = sorted(
        p
        for p in seen
        if recorded.get(p) != (" ".join(entries[p]) if p in entries else "")
        and not ignorable(p, recorded, ignore, docs, gone)
    )
    if overlap:
        shown = ", ".join(overlap[:8]) + (f" (+{len(overlap) - 8} more)" if len(overlap) > 8 else "")  # noqa: PLR2004
        return Verdict(DELTA, f"re-review only the overlapping files: {shown}", tuple(overlap))
    return Verdict(
        KEEP,
        f"every file this review covered is unchanged in the tree being completed (the item's "
        f"own patch-id {own_id[:12]}) and the files the tree move brought in are disjoint",
    )
