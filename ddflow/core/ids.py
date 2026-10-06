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
from dataclasses import dataclass
from typing import Any

from ..config import ID_PREFIXES, Config, id_problem


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
    if token == "prefix" and "prefix" in fields:
        return str(fields["prefix"])
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


def render(cfg: Config, kind: str, *, check: bool = True, used: Any = (), **fields: Any) -> str:
    """The id ``kind``'s `[ids]` template makes of ``fields``. ``{prefix}``, ``{hash}``
    (from ``hash_parts``), ``{time}``, ``{date}`` and ``{pid}`` are filled here when not
    given; every other token must be passed. The result is held to the id characters
    (`config.id_problem`): a value that makes it unusable raises ValueError. ``check=
    False`` is for ids spelled by a source the project already uses (an imported file's
    headings), which keep that spelling as they always have.

    A template holding `{seq}` with no ``seq`` given takes the next free number among
    ``used`` (every id taken); `make` adds the taken-id check and suffixing on top."""
    template = TEMPLATE_OF[kind](cfg)
    if "{seq}" in template and "seq" not in fields:
        fields = {**fields, "seq": next_seq(template, used, **_fixed(fields))}
    minted = re.sub(r"\{([^{}]*)\}", lambda m: _token_value(kind, m.group(1), fields), template)
    # The template was valid; the VALUES may still not be (an empty slug between two
    # dots, a caller's prefix with a slash): an id is a file name, a branch name and a
    # glob token, so a bad one is refused here rather than written into the log.
    if check and (problem := id_problem(minted)):
        raise ValueError(f"the {kind} id {minted!r} {problem}")
    return minted


# -- the id service (B-id-generator) ---------------------------------------------------


@dataclass(frozen=True)
class Minted:
    """What `make` minted. ``id`` is the internal id every event names; ``key`` is what
    people see and type. They differ only when a `{seq}` template is configured over a
    kind whose shipped id has no `{seq}` (D-id-schemes-final, 3): the record keeps the
    id ddflow always minted, and the key is resolved to it (B-id-aliases)."""

    id: str
    key: str


#: The events that record a minted record and may carry its `key` (`key_field`).
KEYED_EVENTS = frozenset(
    {
        "bug.found",
        "lesson.recorded",
        "research.recorded",
        "decision.recorded",
        "memory.recorded",
        "job.started",
        "session.started",
        "task.added",
        "phase.added",
    }
)


def taken(state: Any, events: Any = ()) -> dict[str, str]:
    """Every id in the fold -- and every key a record was minted under (an event's
    ``key``) -- -> the kind holding it: the one namespace every minted id is checked
    against (D-id-schemes-final, 2). Removed and finished records included: an id is
    never reused."""
    out: dict[str, str] = {}
    for ev in (events() if callable(events) else events) or ():
        if getattr(ev, "kind", "") not in KEYED_EVENTS:
            continue  # a `key` elsewhere (a trigger's dedupe key) is not an id
        key = ev.data.get("key") if isinstance(getattr(ev, "data", None), dict) else None
        if isinstance(key, str) and key:
            out.setdefault(key, "key")
    for coll, kind in (
        ("items", "item"),
        ("bugs", "bug"),
        ("lessons", "lesson"),
        ("research", "research"),
        ("decisions", "decision"),
        ("memories", "memory"),
        ("jobs", "job"),
        ("sessions", "session"),
    ):
        for rid in getattr(state, coll, {}) or {}:
            out.setdefault(rid, kind)
    return out


def _pattern(template: str) -> re.Pattern[str]:
    """A regex matching the ids `template` mints, `{seq}` captured."""
    parts = re.split(r"(\{[^{}]*\})", template)
    rx = ""
    for part in parts:
        if part == "{seq}":
            rx += r"(?P<seq>\d+)"
        elif part.startswith("{") and part.endswith("}"):
            rx += r".+?"
        else:
            rx += re.escape(part)
    return re.compile(rx)


def next_seq(template: str, used: Any, **fields: Any) -> int:
    """The highest `{seq}` already minted from ``template`` (with ``fields`` fixed, so
    `fix-{parent}-{seq}` counts per bug), plus one; 1 when there is none. Counted over
    every id ever recorded, so a number is never reused."""
    fixed = template
    for name, value in fields.items():
        fixed = fixed.replace("{" + name + "}", str(value))
    rx = _pattern(fixed)
    high = 0
    for rid in used:
        m = rx.fullmatch(rid)
        if m and m.group("seq"):
            high = max(high, int(m.group("seq")))
    return high + 1


def _default(kind: str) -> str:
    from ..config import IdsConfig

    return str(getattr(IdsConfig(), kind))


def _free(candidate: str, used: Any) -> str:
    """``candidate``, or ``candidate-2``, ``-3`` ... -- the first one nobody holds."""
    if candidate not in used:
        return candidate
    n = 2
    while f"{candidate}-{n}" in used:
        n += 1
    return f"{candidate}-{n}"


def make(cfg: Config, kind: str, *, used: Any = (), **fields: Any) -> Minted:
    """Mint ``kind``'s id from its `[ids]` template: the one place ids are made.

    ``used`` is every id already taken (`taken(state)`; the caller holds the fold it is
    about to append to). `{seq}` is allocated as the next free number unless given; a
    taken result gets `-2`, `-3`. A STABLE template (`{digest}`) maps the same content to
    the same id on purpose: a taken one is returned as it is when a record of the same
    kind holds it, and refused (ValueError) when anything else does."""
    template = TEMPLATE_OF[kind](cfg)
    default = _default(kind)
    holders = used if isinstance(used, dict) else dict.fromkeys(used, "")
    if "{digest}" in template:
        key = render(cfg, kind, **fields)
        holder = holders.get(key)
        if holder not in (None, "", _record_kind(kind)):
            raise ValueError(f"the {kind} id {key} is already taken by a {holder}")
        return Minted(key, key)
    key = _free(render(cfg, kind, used=holders, **fields), holders)
    if "{seq}" in template and template != default and "{seq}" not in default:
        # A key over an internal id minted as ever (D-id-schemes-final, 3).
        internal = _free(_render_template(default, kind, fields), holders)
        return Minted(internal, key)
    return Minted(key, key)


def _fixed(fields: dict[str, Any]) -> dict[str, Any]:
    """The fields a sequence is counted under: the content tokens, not the salt."""
    return {k: v for k, v in fields.items() if k in ("parent", "phase", "env", "slug", "prefix")}


def _record_kind(kind: str) -> str:
    """The `taken` kind a minted kind's records are folded as."""
    return {"ci_bug": "bug", "fix_task": "item", "fix_task_followup": "item"}.get(kind, kind)


def _render_template(template: str, kind: str, fields: dict[str, Any]) -> str:
    return re.sub(r"\{([^{}]*)\}", lambda m: _token_value(kind, m.group(1), fields), template)


def bugs_named_by_fix_task(cfg: Config | None, item_id: str) -> list[str]:
    """The bug ids a fix-task id may name -- `fix-<bug>` or a follow-up `fix-<bug>-2` by
    default -- read back through the `[ids].fix_task` and `fix_task_followup` templates
    (the configured ones and the shipped ones), never by a prefix written elsewhere. A
    candidate, not a verdict: the caller keeps only ids that are bugs."""
    templates: list[str] = []
    for c in [cfg] if cfg is not None else []:
        templates += [TEMPLATE_OF["fix_task_followup"](c), TEMPLATE_OF["fix_task"](c)]
    templates += [_default("fix_task_followup"), _default("fix_task")]
    out: list[str] = []
    for template in templates:
        if "{parent}" not in template:
            continue
        rx = _pattern(template.replace("{parent}", "\x00"))
        # the first {parent} captures, any repeat must be the same text
        pattern = rx.pattern.replace(re.escape("\x00"), "(?P<parent>.+)", 1)
        pattern = pattern.replace(re.escape("\x00"), "(?P=parent)")
        if (m := re.fullmatch(pattern, item_id)) and m.group("parent") not in out:
            out.append(m.group("parent"))
    return out


def refile(base: str, sha: str) -> str:
    """A stable id filed AGAIN after its first record closed (a CI check failing after its
    bug was fixed): the stable id and the failing commit's short sha."""
    return "-".join((base, sha[:7]))


def is_filing_of(rid: str, base: str) -> bool:
    """Whether ``rid`` is ``base`` itself or exactly one of its re-filings (`refile`):
    the suffix must be a short sha (4 to 7 hex digits), so another stable id that merely
    starts with ``base`` (whose own suffix is a ten-digit digest) is not taken for one."""
    return rid == base or re.fullmatch(re.escape(base) + r"-[0-9a-f]{4,7}", rid) is not None


def confirm(cfg: Config, kind: str, minted: Minted, *, used: Any, **fields: Any) -> Minted:
    """``minted`` re-checked against ``used`` read again under the log lock: a key another
    writer took since the mint is minted afresh (the internal id is time-salted and
    keeps). Call it inside the transaction that appends the record; ``used`` may be a
    callable, read only when there is a key to check."""
    if minted.key == minted.id:
        return minted  # a time-salted or caller-named id: nothing to re-check
    used = used() if callable(used) else used
    holders = used if isinstance(used, dict) else dict.fromkeys(used, "")
    if minted.key not in holders:
        return minted
    again = make(cfg, kind, used=holders, **fields)
    return Minted(minted.id, again.key)


def mint(
    cfg: Config, state: Any, kind: str, *, events: Any = (), given: str = "", **fields: Any
) -> Minted:
    """The id for a new record of ``kind``: the caller's own (``given``) when it named one,
    else `make` checked against everything the fold holds (`taken`)."""
    if given:
        return Minted(given, given)
    return make(cfg, kind, used=taken(state, events), **fields)


def key_field(minted: Minted) -> dict[str, str]:
    """``{"key": ...}`` for the record's event when its key is not its id, else ``{}``."""
    return {"key": minted.key} if minted.key != minted.id else {}


def used_now(log: Any) -> Callable[[], dict[str, str]]:
    """`taken` of the log as it is NOW, for `confirm` under the log's lock (lazy)."""

    def read() -> dict[str, str]:
        from .model import fold

        events = log.read_all()
        return taken(fold(events, strict=False), events)

    return read
