"""The `[fasttrack]` section: the tiny-fix fast-track limits (D-fast-track-answers).

The LIMITS a completed item's ACTUAL diff is judged against, post-hoc, and the knobs the
`ddflow metrics tiny` view reads to bucket items and report the fast track's evidence.
The guard that demotes an over-limit item (FT-guard) and the bundler that groups tiny
fixes (FT-bundle) land their own knobs here when they ship. The fast track only ever
LIGHTENS gates on a diff inside these limits; demote never promotes.
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
        doc="Most source lines (insertions plus deletions, tests/docs/generated pins and README not counted) a completed change may have and still be called tiny-cold. `ddflow metrics tiny` buckets an item by the changed lines its gate evidence recorded; the file-count limits a tiny-cold change also bounds are declared by FT-guard, whose numstat probe the metrics view reports as not-measured until it is wired.",
        check=int_at_least(0),
    )
