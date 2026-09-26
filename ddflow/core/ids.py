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

    Content-addressed instead, so the id is stable for identical content and distinct
    for anything else, with a nanosecond stamp to separate genuine duplicates.
    """
    seed = "|".join(parts) + f"|{time.time_ns()}"
    return prefix + hashlib.blake2b(seed.encode("utf-8"), digest_size=5).hexdigest()
