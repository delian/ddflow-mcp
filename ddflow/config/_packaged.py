"""The packaged layer: `templates/default.toml`, read under every other layer (CE-defaults-file).

The file is generated from the section declarations and pinned to them, so it normally says
what the dataclass defaults already say. Each knob is therefore COMPARED and only a value that
differs goes through `Config._apply_knob`: coercing all of them costs more than the parse, on
every load, for nothing (R555dc3c874). A differing value keeps the source name "default",
because that is what the layer is to everything that asks whether anyone set a knob.

The file is parsed once per process (`Config.load` runs several times per command). Until
CE-defaults-source moves the values here the dataclasses are a complete fallback, so a file
that is damaged, absent or wrong warns and is passed over: refusing would also stop
`ddflow pins regenerate`, the command that repairs it.
"""

from __future__ import annotations

import copy
import functools
import sys
import tomllib
from typing import Any

from ..config_sections._layers import PACKAGED_DEFAULTS, ConfigConflict, read_toml

_REPAIR = "the built-in defaults apply; repair it with `ddflow pins regenerate --only default-toml`"

#: What this process has already said: `Config.load` runs several times per command.
_SAID: set[str] = set()


def _say(problem: str) -> None:
    """``problem`` on stderr, once per process, so `--json` and MCP replies stay parseable."""
    if problem not in _SAID:
        _SAID.add(problem)
        print(f"ddflow: warning: {problem}; {_REPAIR}", file=sys.stderr)


@functools.cache
def packaged() -> dict[str, Any]:
    """The parsed packaged file, or `{}` (having warned) when it can not be read."""
    try:
        return read_toml(PACKAGED_DEFAULTS)
    except (OSError, UnicodeDecodeError, ConfigConflict, tomllib.TOMLDecodeError) as exc:
        _say(f"cannot read the packaged {PACKAGED_DEFAULTS}: {exc}")
        return {}


def apply_packaged(cfg: Any) -> None:
    """Lay the packaged file under ``cfg``, a `Config` holding only its dataclass defaults.

    A section or knob this code does not have is skipped without a word: the pin reports it,
    and a branch that removed a knob must not warn on every command until it regenerates.
    `_FOREIGN_TABLES` such as `[gate.*]` are not sections, so their readers stay in charge.
    """
    sections = set(cfg._sections())
    for sec, table in packaged().items():
        if sec not in sections or not isinstance(table, dict):
            continue
        target = getattr(cfg, sec)
        for knob, raw in table.items():
            if not hasattr(target, knob) or getattr(target, knob) == raw:
                continue
            try:  # a copy: the parse is shared by every load in this process
                cfg._apply_knob(sec, knob, copy.deepcopy(raw), "default", False)
            except (ValueError, TypeError) as exc:
                _say(f"the packaged {PACKAGED_DEFAULTS} holds a bad {sec}.{knob}: {exc}")
