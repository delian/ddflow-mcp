"""Enum and value knobs: the value sets `Config.check` refuses anything outside.

The tables are derived from the `knob(choices=, strictest=)` declarations on each
section's fields (`_docs.declared_tables`), so a knob's allowed values live beside its
field rather than in a comment. `tests/test_config_enum_knobs.py` finds any comment or
knob doc listing `a | b` that they do not cover.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config_sections._docs import DECLARED, KNOB_DOCS, declared_tables
from ..config_sections.ids import ID_KINDS
from ..config_sections.schedule import SIGNALS, default_signals, strictest_signals
from ..config_sections.triggers import TRIGGERS_STRICTEST_NUMBER

#: Every ENUM knob and the values it may hold (bug Beea0744a7b). Each entry gets its check
#: in `_KNOB_CHECKS` from here, so a knob whose choices lived only in a comment
#: (`# block | warn | off`) can no longer take a typo that quietly behaves as some other
#: value. A new enum knob is declared on its field with `knob(choices=, strictest=)` in
#: `config_sections/`, not in a comment (the tables below are built from those
#: declarations); `tests/test_config_enum_knobs.py` finds any comment or knob doc listing
#: `a | b` that they do not cover.
_DC, _DS, _DO, _DK = declared_tables()  # the knobs declared on their fields (_docs.knob)
KNOB_CHOICES: dict[str, tuple[str, ...]] = {
    **_DC,
}

#: The value each enum knob takes when a config FILE gives it one this code does not know
#: (decision D-enum-fallback-strict, superseding the fall-back-to-default part of
#: D9b8061fd38): its STRICTEST allowed value, so a typo in a deliberately tightened
#: setting makes ddflow more careful. For a knob with no safety dimension the "strictest"
#: is the value that does the most checking or changes least; the reason is appended to
#: each knob's doc below. One rule outranks strictness: never a value that acts outside
#: this clone (`KNOB_OUTWARD`; D-fallback-no-remote). Where the two conflict -- `pr`
#: waits for approval but pushes, `remote` claims exclusively but writes remote refs --
#: the fallback stays local, and a typo there loosens approval or exclusivity until fixed.
#: `tests/test_config_enum_knobs.py` requires an entry for every KNOB_CHOICES key here
#: and in `KNOB_OUTWARD`, and that no fallback is an outward value.
KNOB_STRICTEST: dict[str, tuple[str, str]] = {
    **_DS,
}

#: Each enum knob's values that make ddflow act OUTSIDE this clone: a push, a pull
#: request, remote claim refs, a merge on the forge. No `KNOB_STRICTEST` fallback may be
#: one (D-fallback-no-remote): outward behaviour happens only when someone sets it
#: correctly, on purpose. Written out for EVERY KNOB_CHOICES key, empty when none, and
#: the test requires the two key sets to match: a new knob fails it until its author
#: answers the question here (a seeded default would answer it for them).
KNOB_OUTWARD: dict[str, frozenset[str]] = {
    **_DO,
}

#: Numeric knobs whose bad FILE value takes the strictest value (D-trigger-cap-knob): as
#: `KNOB_STRICTEST`, kept apart because that table must match `KNOB_CHOICES`.
KNOB_STRICTEST_NUMBER: dict[str, tuple[int, str]] = {**TRIGGERS_STRICTEST_NUMBER}

for _key, (_value, _why) in {**KNOB_STRICTEST, **KNOB_STRICTEST_NUMBER}.items():
    KNOB_DOCS[_key] = (
        f"{KNOB_DOCS[_key]} An unrecognised value in a config file is warned about, reported "
        f"by `doctor` and falls back to '{_value}', the strictest ({_why}); `config --set`, "
        "`ddflow_configure` and the environment refuse it, and so does loading ddflow's own "
        "source tree, where the file and the code are one commit."
    )
del _key, _value, _why


def strictest(key: str, below: Any = None) -> Any:
    """What enum or number knob `key` (or the signals table, from `below`) falls back to."""
    if key == SIGNALS:
        return strictest_signals(below if isinstance(below, dict) else default_signals())
    if key in KNOB_STRICTEST_NUMBER:
        return KNOB_STRICTEST_NUMBER[key][0]
    return KNOB_STRICTEST[key][0]


#: Knobs whose VALUE set can grow in a later release (every enum, and `export.tables`, whose
#: sub-tables carry enums of their own), so a config FILE carrying a value this version
#: does not know is tolerated with a warning rather than refused -- an enum knob then
#: takes its strictest value (`KNOB_STRICTEST`), as does a number knob with one
#: (`KNOB_STRICTEST_NUMBER`); the write paths (`config --set`,
#: `ddflow_configure`) and the environment still refuse it.
_TOLERANT_VALUES = frozenset(
    {*KNOB_CHOICES, *KNOB_STRICTEST_NUMBER, "export.tables", "bugs.phase", SIGNALS}
    | {f"ids.{k}" for k in ID_KINDS}
)
_STRICT_FALLBACK = {*KNOB_STRICTEST, *KNOB_STRICTEST_NUMBER, SIGNALS}  # values `strictest` has


def _one_of(allowed: tuple[str, ...]) -> Callable[[Any], str]:
    """The check for an enum knob: "" for a declared value, else the list it must be in."""
    return lambda v: "" if v in allowed else f"must be one of {', '.join(allowed)}"


#: Knobs whose TYPE is not the whole contract: "" means valid, else why not. Checked on
#: load and by `Config.check`, so `config set` refuses the value instead of writing it.
#: `max_behind = 0` read as "never warn" would be a switch hidden in a threshold -- the
#: silent-knob-drop class -- when `behind = "off"` already says it plainly.
_VALUE_CHECKS: dict[str, Callable[[Any], str]] = {**_DK}

#: Every check: one derived from each KNOB_CHOICES entry, and the ones declared with
#: `knob(check=)` (`_VALUE_CHECKS` above).
#: The two never share a key (`tests/test_config_enum_knobs.py` asserts it), so neither can
#: silently shadow the other.
_KNOB_CHECKS: dict[str, Callable[[Any], str]] = {
    **{key: _one_of(allowed) for key, allowed in KNOB_CHOICES.items()},
    **_VALUE_CHECKS,
}

#: Checked list and table knobs whose set of MEMBERS grows with releases (`knob(members=)`):
#: for a config FILE's value, the filter returns it without the members this code does not
#: know, and what it dropped; they are noted (`Config.ignored_members`), never a failure.
_MEMBER_FILTERS: dict[str, Callable[[Any], tuple[Any, list[str]]]] = {
    key: meta.members for key, meta in DECLARED.items() if meta.members is not None
}

#: Knobs the committed `file` layer may not set (`knob(operator_only=True)`): operator-only
#: tables such as `[agent].roles` and `[agent].capability_verbs`, whose committed value would
#: let a cloned project widen a token holder's authority (B-token-role-widened).
_OPERATOR_ONLY: frozenset[str] = frozenset(
    key for key, meta in DECLARED.items() if meta.operator_only
)
