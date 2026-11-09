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

from ..infra import worktree as W
from .handoff.refs import patch_id as _patch_id

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


def classify(recorded: dict[str, str] | None, changed: Sequence[str] | None, entries, own_id: str) -> Verdict:
    """Keep, delta or stale for a round whose `reviewed_paths` is ``recorded``.

    ``entries`` is the current tree ({path: (mode, oid)}) the round is compared against, or
    None when it cannot be read. ``own_id`` is `own_patch_id` of the item's own change ("" =
    cannot be told). Never reuses on doubt: a missing `own_id`, an unreadable tree or an
    empty `recorded` all return `stale` so the review is re-reviewed whole."""

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
    seen = set(recorded)
    own = seen | set(changed or [])
    moved = sorted(
        p for p in own if recorded.get(p) != (" ".join(entries[p]) if p in entries else "")
    )
    if not moved:
        return Verdict(
            KEEP,
            f"the item's own change (patch-id {own_id[:12]}) is unchanged and the files the "
            f"tree move brought in are disjoint from the {len(seen)} file(s) this review covered",
        )
    shown = ", ".join(moved[:8]) + (f" (+{len(moved) - 8} more)" if len(moved) > 8 else "")  # noqa: PLR2004
    return Verdict(DELTA, f"re-review only the overlapping files: {shown}", tuple(moved))
