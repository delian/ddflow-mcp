"""The packaged `default.toml`, generated from the declarations (CE-defaults-file).

Every knob is a field of one of `Config`'s section dataclasses and every default gate a
`GateDef`, so the file is rendered from exactly those, in `Config`'s order. It holds values
only: `ddflow config --explain` has what each one means. The `default-toml` pin keeps it
current; rewrite it with

    uv run python -m ddflow.views.default_toml          (--check: exit 1 when it is stale)
"""

from __future__ import annotations

import sys
from dataclasses import fields

from ..config import Config
from ..config_sections._layers import PACKAGED_DEFAULTS
from ..infra.fsio import atomic_write
from ..infra.tomlcfg import value
from ..services.gates.defs import DEFAULT_GATES, GateDef

HEADER = """\
# ddflow's packaged defaults: every knob and the default workflow, the lowest config layer.
# GENERATED from the section declarations: do not edit. To change a default for a project,
# set it in .ddflow/config.toml. Rewrite this file with `ddflow config defaults --write`.
# What each knob means: `ddflow config --explain`.
"""

#: Not written for a gate: its table name is its id, and `[gates].required` decides `required`.
_GATE_DERIVED = frozenset({"id", "required"})


def _gate_lines(gid: str, gate: GateDef) -> list[str]:
    """``[gate.<gid>]`` with what differs from a bare `GateDef`, as a project's overlay is."""
    bare = GateDef(id=gid)
    return [
        f"[gate.{gid}]",
        *(
            f"{f.name} = {value(getattr(gate, f.name))}"
            for f in fields(gate)
            if f.name not in _GATE_DERIVED and getattr(gate, f.name) != getattr(bare, f.name)
        ),
    ]


def render() -> str:
    """The file: every section's knobs in `Config`'s order, then the default gates."""
    cfg = Config()
    blocks = [HEADER.rstrip("\n")]
    for sec in cfg._sections():
        section = getattr(cfg, sec)
        lines = (f"{f.name} = {value(getattr(section, f.name))}" for f in fields(section))
        blocks.append("\n".join((f"[{sec}]", *lines)))
    blocks.extend("\n".join(_gate_lines(gid, g)) for gid, g in DEFAULT_GATES.items())
    return "\n\n".join(blocks) + "\n"


def current() -> str:
    """The file as it is on disk, "" when there is none (or it is not a regular file, which
    a read could block on). Undecodable bytes are replaced, so a damaged file reads as stale
    and the write that repairs it can still run."""
    if not PACKAGED_DEFAULTS.is_file():
        return ""
    try:
        return PACKAGED_DEFAULTS.read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""


def write() -> bool:
    """Rewrite the file when it is stale; whether it changed."""
    text = render()
    if current() == text:
        return False
    atomic_write(PACKAGED_DEFAULTS, text)
    return True


def main(argv: list[str]) -> int:
    """Rewrite the file, or with ``--check`` exit 1 when it is not what `render` says."""
    if "--check" in argv:
        if current() == render():
            return 0
        print(f"{PACKAGED_DEFAULTS} is stale: ddflow pins regenerate --only default-toml")
        return 1
    print(f"{PACKAGED_DEFAULTS}: {'rewritten' if write() else 'current'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
