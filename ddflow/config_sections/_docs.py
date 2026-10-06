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
`KNOB_CHOICES`, `KNOB_STRICTEST`, `KNOB_OUTWARD` and the load/`config --set` checks. The
older form -- a bare field, a separate `_doc(...)` call and an entry in each of those
tables in config.py -- still works while the sections move over; a ratchet
(tests/test_config_knobs_declared.py) counts what is left of it and only lets it shrink,
and a knob declared both ways is an error at import.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import MISSING, dataclass, field, fields
from typing import Any

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


#: Every knob declared through `knob()`, by "section.knob", in declaration order.
DECLARED: dict[str, Knob] = {}


def knob(
    default: Any = MISSING,
    *,
    doc: str,
    factory: Callable[[], Any] | Any = MISSING,
    choices: tuple[str, ...] = (),
    strictest: tuple[str, str] | None = None,
    outward: frozenset[str] | set[str] = frozenset(),
    check: Callable[[Any], str] | None = None,
) -> Any:
    """A dataclass field that carries its knob declaration (see the module docstring).
    `factory` for a mutable default (a list or dict), as `field(default_factory=...)`."""
    if choices and strictest is None:
        raise ValueError("an enum knob (choices) must name its strictest fallback")
    if strictest is not None and strictest[0] not in choices:
        raise ValueError(f"strictest {strictest[0]!r} is not one of {choices}")
    if set(outward) - set(choices):
        raise ValueError(f"outward values {sorted(set(outward) - set(choices))} are not choices")
    meta = {KNOB: Knob(doc, tuple(choices), strictest, frozenset(outward), check)}
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
            KNOB_DOCS[key] = meta.doc
            DECLARED[key] = meta
        return cls

    return register


def _doc(section: str, knob: str, text: str) -> None:
    """The older declaration: a doc beside a bare field. Being retired (see above)."""
    key = f"{section}.{knob}"
    if key in DECLARED:
        raise ValueError(f"knob {key} is declared twice")
    KNOB_DOCS[key] = text


def declared_tables() -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, str]],
    dict[str, frozenset[str]],
    dict[str, Callable[[Any], str]],
]:
    """`DECLARED` as the tables `ddflow.config` keeps: (choices, strictest, outward, checks),
    each holding only the knobs that declare one. Called once every section is imported."""
    enums = {k: m for k, m in DECLARED.items() if m.choices}
    return (
        {k: m.choices for k, m in enums.items()},
        {k: m.strictest for k, m in enums.items() if m.strictest is not None},
        {k: m.outward for k, m in enums.items()},
        {k: m.check for k, m in DECLARED.items() if m.check is not None},
    )
