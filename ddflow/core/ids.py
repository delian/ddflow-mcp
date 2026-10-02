"""Collision-free identifiers for records the caller did not name.

Pure: hashing and a clock, no disk. Lives here rather than in `surfaces/` because the
`api` layer generates ids too, and a layer may not reach up into a surface to do it —
which is how this function came to be imported across the layer rule in the first
place.
"""

from __future__ import annotations

import hashlib
import time


def auto_id(prefix: str, *parts: str) -> str:
    """A collision-free auto id.

    Second-resolution timestamps (`f"L{int(time.time())}"`) collide whenever two items
    are created in the same second -- which a script, a loop, or an agent recording two
    lessons from one bug hunt does routinely. The collision is SILENT: the second
    record overwrites the first in the fold, so the entry simply disappears. Measured:
    7 lessons added in one second, 2 survived.

    So the id is salted with a nanosecond clock reading: the SAME text filed twice gets
    two DIFFERENT ids, on purpose. Two real reports of identical text stay two records
    until the add-time duplicate check (D-no-duplicates) catches them -- an exact copy of
    an open record is merged into it as an extension, an exact copy of a closed one is
    filed as a new record linked to it, both without asking. Only an id the caller names is stable, and a re-report under
    one merges into its record. The text is hashed in so ids differ across content too,
    but nothing may rely on an auto id being reproducible. Do not change the format:
    existing logs hold these ids and must replay identically.
    """
    seed = "|".join(parts) + f"|{time.time_ns()}"
    return prefix + hashlib.blake2b(seed.encode("utf-8"), digest_size=5).hexdigest()
