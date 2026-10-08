"""The knob registry every section writes to as it is defined. `ddflow.config` re-exports
`KNOB_DOCS` (the same dict); `config --explain` renders it.

A knob is declared ONCE, on its dataclass field (B-uni-knobs, D-unify 4):

    @declare("lease")
    @dataclass
    class LeaseConfig:
        reclaim_policy: str = knob(
            "report",
            doc="'report' (default) never steals an expired lease ...",
            choices=("report", "auto"),
            strictest=("report", "never steals a lease, so a crashed agent's work survives"),
        )

`declare` copies each field's doc into `KNOB_DOCS` and its choices, strictest fallback,
outward values and value check into `DECLARED`, from which `ddflow.config` builds
`KNOB_CHOICES`, `KNOB_STRICTEST`, `KNOB_OUTWARD` and the load/`config --set` checks. Every
section is declared this way; the older form (a bare field, a separate `_doc(...)` call and
an entry in each of those tables in config.py) is gone, and a ratchet
(tests/test_config_knobs_declared.py, baselines 0) fails if a table entry comes back. A knob
declared twice is an error: at import for its doc, and in that test for a table entry left
behind in config.py (where it would silently shadow the declared value).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import MISSING, dataclass, field, fields
from typing import Any

from ._compat import MIN_REMOVED_IN, check_rename, version_tuple

# --------------------------------------------------------------------------------------
# Knob documentation lives beside the knob, in this dict, keyed "section.knob".
# `ddflow config --explain` renders it.  A knob with no entry here fails a ratchet test
# (tests/test_config.py::test_every_knob_is_documented), so the docs cannot silently rot.
# --------------------------------------------------------------------------------------
KNOB_DOCS: dict[str, str] = {}

#: The dataclass-field metadata key `knob()` stores its declaration under.
KNOB = "ddflow.knob"


@dataclass(frozen=True)
class Knob:
    """What a knob declares besides its type and default."""

    doc: str
    #: An enum knob's allowed values (`KNOB_CHOICES`); empty for any other knob.
    choices: tuple[str, ...] = ()
    #: (value, why) an enum knob falls back to when a config FILE holds an unknown value
    #: (D-enum-fallback-strict, `KNOB_STRICTEST`); required with `choices`.
    strictest: tuple[str, str] | None = None
    #: The choices that act outside this clone (D-fallback-no-remote, `KNOB_OUTWARD`).
    outward: frozenset[str] = frozenset()
    #: "" for a valid value, else why not -- for a knob whose type is not the whole contract.
    check: Callable[[Any], str] | None = None
    #: Earlier spellings of this knob, as full ``"section.knob"`` keys (D-compat): still read,
    #: with a warning, and moved to this key on the next config write. Needs ``since``.
    renamed_from: tuple[str, ...] = ()
    #: The release that renamed it, and the earliest release the old key may stop working.
    since: str = ""
    removed_in: str = MIN_REMOVED_IN
    #: For a checked list or table whose set of members grows with releases: given a config
    #: FILE's value, ``(the value without the members this version does not know, what was
    #: dropped)``. Dropped members are noted, not refused (D-compat 2); the write paths and
    #: the environment still refuse them.
    members: Callable[[Any], tuple[Any, list[str]]] | None = None
    #: The release that changed this knob's DEFAULT, and the default before it. A value
    #: anyone set is never changed by the move (D-upgrade-config-changes): this is the
    #: declaration `config --explain` shows.
    default_changed_in: str = ""
    default_was: Any = None


#: Every knob declared through `knob()`, by "section.knob", in declaration order.
DECLARED: dict[str, Knob] = {}
#: The module whose `declare()` registered each of them (a section declares only its own).
DECLARED_IN: dict[str, str] = {}
#: Old key -> (current key, since, removed_in) for every ``renamed_from`` (D-compat).
RENAMED: dict[str, tuple[str, str, str]] = {}


def knob(  # noqa: PLR0913 -- one keyword per declaration facet; the call sites read as a table
    default: Any = MISSING,
    *,
    doc: str,
    factory: Callable[[], Any] | Any = MISSING,
    choices: tuple[str, ...] = (),
    strictest: tuple[str, str] | None = None,
    outward: frozenset[str] | set[str] = frozenset(),
    check: Callable[[Any], str] | None = None,
    renamed_from: tuple[str, ...] | list[str] = (),
    since: str = "",
    removed_in: str = MIN_REMOVED_IN,
    members: Callable[[Any], tuple[Any, list[str]]] | None = None,
    default_changed_in: str = "",
    default_was: Any = None,
) -> Any:
    """A dataclass field that carries its knob declaration (see the module docstring).
    `factory` for a mutable default (a list or dict), as `field(default_factory=...)`."""
    if default_changed_in:
        version_tuple(default_changed_in)
        doc = f"{doc} (Default changed in {default_changed_in}; it was {default_was!r}.)"
    if choices and strictest is None:
        raise ValueError("an enum knob (choices) must name its strictest fallback")
    if strictest is not None and strictest[0] not in choices:
        raise ValueError(f"strictest {strictest[0]!r} is not one of {choices}")
    if set(outward) - set(choices):
        raise ValueError(f"outward values {sorted(set(outward) - set(choices))} are not choices")
    if renamed_from:
        check_rename("knob renamed_from", since, removed_in)
        for old in renamed_from:
            if old.count(".") != 1 or not all(old.split(".")):
                raise ValueError(f"renamed_from {old!r} is not a 'section.knob' key")
    meta = {
        KNOB: Knob(
            doc,
            tuple(choices),
            strictest,
            frozenset(outward),
            check,
            tuple(renamed_from),
            since,
            removed_in,
            members,
            default_changed_in,
            default_was,
        )
    }
    if factory is not MISSING:
        return field(default_factory=factory, metadata=meta)
    return field(default=default, metadata=meta)


def declare(section: str) -> Callable[[type], type]:
    """Class decorator, above `@dataclass`: register each `knob()` field of `[section]`."""

    def register(cls: type) -> type:
        for f in fields(cls):
            meta = f.metadata.get(KNOB)
            if meta is None:
                continue
            key = f"{section}.{f.name}"
            if key in KNOB_DOCS or key in DECLARED:
                raise ValueError(f"knob {key} is declared twice")
            if key in RENAMED:  # the order sections are declared in must not matter
                raise ValueError(
                    f"{key} is renamed_from of {RENAMED[key][0]} but is also a live key"
                )
            KNOB_DOCS[key] = meta.doc
            DECLARED[key] = meta
            DECLARED_IN[key] = cls.__module__
            for old in meta.renamed_from:
                if old == key or old in DECLARED:
                    raise ValueError(f"{old} is renamed_from of {key} but is also a live key")
                if old in RENAMED:
                    raise ValueError(f"{old} is renamed_from of {key} and of {RENAMED[old][0]}")
                RENAMED[old] = (key, meta.since, meta.removed_in)
        return cls

    return register


def int_at_least(n: int) -> Callable[[Any], str]:
    """The `check=` of an integer knob that must be at least ``n``."""
    return lambda v: (
        ""
        if isinstance(v, int) and not isinstance(v, bool) and v >= n
        else f"must be an integer >= {n}"
    )


def declared_tables() -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, str]],
    dict[str, frozenset[str]],
    dict[str, Callable[[Any], str]],
]:
    """`DECLARED` as the tables `ddflow.config` keeps: (choices, strictest, outward, checks).
    choices, strictest and outward have an entry for every ENUM knob -- outward is written
    out even when empty, as `KNOB_OUTWARD` requires one per `KNOB_CHOICES` key -- and checks
    one for each knob that declares a check. Called once every section is imported."""
    enums = {k: m for k, m in DECLARED.items() if m.choices}
    return (
        {k: m.choices for k, m in enums.items()},
        {k: m.strictest for k, m in enums.items() if m.strictest is not None},
        {k: m.outward for k, m in enums.items()},
        {k: m.check for k, m in DECLARED.items() if m.check is not None},
    )
