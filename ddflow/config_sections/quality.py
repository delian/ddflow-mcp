"""The `[quality]` section: size and shape limits the repository holds itself to.

Read by `services.file_size` (the ratchet `tests/test_file_size_ratchet.py` and the doctor
note). Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("quality")
@dataclass
class QualityConfig:
    """Limits on the code itself, so a lease covers a small file and a small-context model can read it."""

    max_file_bytes: int = knob(
        30000,
        doc="A tracked .py file (source and tests) larger than this many BYTES (default 30000, about 7k tokens) is OVER the limit (r-small-files): split it by cohesion into a package that keeps the old import path. `ddflow doctor` lists the files over it; tests/test_file_size_ratchet.py fails a new one and an exempt one that grew (the exemptions are the shrink-only baseline tests/guard_baselines/file_size_baseline.txt). An organisation that wants the limit held for every clone sets and locks it in its shared config. 0 turns the check off.",
        check=int_at_least(0),
    )
