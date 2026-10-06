"""Collision-free identifiers for records the caller did not name.

Pure: hashing and a clock, no disk. Lives here rather than in `surfaces/` because the
`api` layer generates ids too, and a layer may not reach up into a surface to do it —
which is how this function came to be imported across the layer rule in the first
place.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Callable
from typing import Any

from ..config import ID_PREFIXES, Config


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
    filed as a new record linked to it, both without asking -- under the default
    `[dedupe].on_match = "ask"`; with `warn` or `off` they stay two unlinked records. Only an id the caller names is stable, and a re-report under
    one merges into its record. The text is hashed in so ids differ across content too,
    but nothing may rely on an auto id being reproducible. Do not change the format:
    existing logs hold these ids and must replay identically.
    """
    seed = "|".join(parts) + f"|{time.time_ns()}"
    return prefix + hashlib.blake2b(seed.encode("utf-8"), digest_size=5).hexdigest()


def _hash(parts: tuple[str, ...] | list[str]) -> str:
    """`auto_id`'s ten hex digits, without its prefix: blake2b over the parts salted
    with a nanosecond clock."""
    return auto_id("", *parts)


def _token_value(kind: str, token: str, fields: dict[str, Any]) -> str:
    if token == "prefix":
        if kind not in ID_PREFIXES:
            raise ValueError(f"{{prefix}} is not defined for {kind} ids")
        return ID_PREFIXES[kind]
    if token == "hash":
        return _hash(tuple(fields.get("hash_parts", ())))
    if token == "time":
        at = fields.get("time")
        return time.strftime("%Y%m%dT%H%M%S", time.gmtime(time.time() if at is None else at))
    if token == "date":
        at = fields.get("time")
        return time.strftime("%Y%m%d", time.gmtime(time.time() if at is None else at))
    if token == "pid":
        return str(int(fields["pid"]) if "pid" in fields else os.getpid())
    if token not in fields:
        raise ValueError(f"the {kind} id template needs {{{token}}}, and no value was given")
    return str(fields[token])


#: Each kind's template, read off the loaded config by name (so every `[ids]` knob is a
#: visible read, and a kind with no entry here is a KeyError, not a silent default).
TEMPLATE_OF: dict[str, Callable[[Config], str]] = {
    "bug": lambda c: c.ids.bug,
    "lesson": lambda c: c.ids.lesson,
    "research": lambda c: c.ids.research,
    "decision": lambda c: c.ids.decision,
    "memory": lambda c: c.ids.memory,
    "job": lambda c: c.ids.job,
    "session": lambda c: c.ids.session,
    "fix_task": lambda c: c.ids.fix_task,
    "fix_task_followup": lambda c: c.ids.fix_task_followup,
    "promotion": lambda c: c.ids.promotion,
    "ci_bug": lambda c: c.ids.ci_bug,
    "split_child": lambda c: c.ids.split_child,
    "imported_phase": lambda c: c.ids.imported_phase,
    "imported_task": lambda c: c.ids.imported_task,
}


def render(cfg: Config, kind: str, **fields: Any) -> str:
    """The id ``kind``'s `[ids]` template makes of ``fields``. ``{prefix}``, ``{hash}``
    (from ``hash_parts``), ``{time}``, ``{date}`` and ``{pid}`` are filled here when not
    given; every other token must be passed.

    Rendering only: the id service (B-id-generator) owns sequence allocation and the
    taken-key check under the log lock."""
    template = TEMPLATE_OF[kind](cfg)
    return re.sub(r"\{([^{}]*)\}", lambda m: _token_value(kind, m.group(1), fields), template)
