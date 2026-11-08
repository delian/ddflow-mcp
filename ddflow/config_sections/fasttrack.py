"""The `[fasttrack]` section: the tiny-fix fast-track limits (D-fast-track-answers).

The LIMITS a completed item's ACTUAL diff is judged against, post-hoc, and the knobs the
`ddflow metrics tiny` view reads to bucket items and report the fast track's evidence.
The guard that demotes an over-limit item, and the bundler that groups tiny fixes, read
the same knobs (FT-guard / FT-bundle elsewhere). The fast track only ever LIGHTENS gates
on a diff inside these limits; demote never promotes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("fasttrack")
@dataclass
class FastTrackConfig:
    """Tiny-fix fast-track limits: how small a change must be to run the reduced pipeline."""

    tiny_cold_max_source_lines: int = knob(
        10,
        doc="Most source lines (insertions plus deletions, tests/docs/generated pins and README not counted) a completed change may have and still be called tiny-cold. `ddflow metrics tiny` buckets an item by the changed lines its gate evidence recorded; the file-count limits below are checked by the guard over the actual diff (git numstat), which the metrics view reports as not-measured until it is wired.",
        check=int_at_least(0),
    )
    tiny_cold_max_files: int = knob(
        6,
        doc="Most files a tiny-cold change may touch (tests, docs, generated pins and README not counted). The guard (FT-guard) checks this over the actual diff; `ddflow metrics tiny` names it under not-measured until the numstat probe feeds it.",
        check=int_at_least(1),
    )
    tiny_cold_max_source_files: int = knob(
        2,
        doc="Most source files a tiny-cold change may touch, of `tiny_cold_max_files`, judged over the actual diff the same way.",
        check=int_at_least(1),
    )
    max_bundle: int = knob(
        10,
        doc="Most path-disjoint tiny-cold fixes one fast-track task, branch, ci run and merge may bundle (D-fast-track-answers). Each fix keeps its own commit, regression test and bug closure; a red bundle is shrunk by ddmin, never reverted whole.",
        check=int_at_least(1),
    )
    bundle_age_s: int = knob(
        2700,
        doc="Seconds a queued tiny-cold fix waits before the bundler may group it with others (45 minutes, D-fast-track-answers). Shorter groups faster and bundles less certain material; longer waits for a fuller, slower bundle.",
        check=int_at_least(0),
    )
