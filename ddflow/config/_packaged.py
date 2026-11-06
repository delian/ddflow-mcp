"""The packaged layer: `templates/default.toml`, read under every other layer (CE-defaults-file).

The file is generated from the section declarations and pinned to them, so it normally says
what the dataclass defaults already say. Each knob is therefore COMPARED and only a value that
differs goes through `Config._apply_knob`: coercing all of them costs more than the parse, on
every load, for nothing (R555dc3c874). A differing value keeps the source name "default",
because that is what the layer is to everything that asks whether anyone set a knob.

The file is parsed once per process (`Config.load` runs several times per command). Until
CE-defaults-source moves the values here the dataclasses are a complete fallback, so a file
that can not be read, or a value no knob can take, warns and is passed over: refusing would
also stop `ddflow pins regenerate`, the command that repairs it. A readable value that merely
differs is applied, because a layer wins over what is under it, and warned about, because
until then any difference is drift.
"""

from __future__ import annotations

import copy
import functools
import sys
from dataclasses import fields
from typing import Any

from ..config_sections._layers import PACKAGED_DEFAULTS, ConfigConflict, read_toml

_REPAIR = "repair it with `ddflow pins regenerate --only default-toml`"
_BUILT_IN = "the built-in defaults apply"

#: Knobs a drift warning names; the rest it counts.
DRIFT_SHOWN = 5

#: What this process has already said: `Config.load` runs several times per command.
_SAID: set[str] = set()


def _say(problem: str) -> None:
    """``problem`` on stderr, once per process, so `--json` and MCP replies stay parseable."""
    if problem not in _SAID:
        _SAID.add(problem)
        print(f"ddflow: warning: {problem}; {_REPAIR}", file=sys.stderr)


def _say_drift(keys: list[str]) -> None:
    more = len(keys) - DRIFT_SHOWN
    named = ", ".join(keys[:DRIFT_SHOWN]) + (f" and {more} more" if more > 0 else "")
    _say(f"the packaged {PACKAGED_DEFAULTS} is stale at {named}")


@functools.cache
def packaged() -> dict[str, Any]:
    """The parsed packaged file, or `{}` (having warned) when it can not be read."""
    try:
        return read_toml(PACKAGED_DEFAULTS)
    except (OSError, ValueError, ConfigConflict) as exc:  # ValueError: bad UTF-8 or TOML
        _say(f"cannot read the packaged {PACKAGED_DEFAULTS}: {exc}; {_BUILT_IN}")
        return {}


def _lay_section(cfg: Any, sec: str, table: dict[str, Any], drift: list[str]) -> None:
    """Lay one `[sec]` table under ``cfg``, adding to ``drift`` each key that is not the
    declaration's. Only a declared field is a knob: every object has a `__class__`."""
    target = getattr(cfg, sec)
    known = {f.name for f in fields(target)}
    for knob, raw in table.items():
        if knob not in known:
            drift.append(f"{sec}.{knob}")
        elif getattr(target, knob) != raw:
            try:  # a copy: the parse is shared by every load in this process
                cfg._apply_knob(sec, knob, copy.deepcopy(raw), "default", False)
                drift.append(f"{sec}.{knob}")
            except (ValueError, TypeError) as exc:
                bad = f"the packaged {PACKAGED_DEFAULTS} holds a bad {sec}.{knob}: {exc}"
                _say(f"{bad}; {_BUILT_IN}")


def apply_packaged(cfg: Any) -> None:
    """Lay the packaged file under ``cfg``, a `Config` holding only its dataclass defaults.

    A key the declarations lack is drift, said on stderr and never put in `unknown_knobs`:
    that list blames the project's own config. `_FOREIGN_TABLES` such as `[gate.*]` are not
    sections, so their readers stay in charge.
    """
    sections = set(cfg._sections())
    drift: list[str] = []
    for sec, table in packaged().items():
        if sec in cfg._FOREIGN_TABLES:
            continue
        if sec not in sections:
            drift.append(f"[{sec}]")
        elif isinstance(table, dict):
            _lay_section(cfg, sec, table, drift)
        else:
            _say(f"the packaged {PACKAGED_DEFAULTS} holds a bad [{sec}]: not a table; {_BUILT_IN}")
    if drift:
        _say_drift(drift)
