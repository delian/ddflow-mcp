"""The interface a ddflow release exposes, as plain JSON (B-uni-compat-tests; D-compat).

ONE file, run by two interpreters, like projection.py: `scripts/ci/compat-matrix surfaces`
runs it under each OLD release's own wheel (writing tests/compat/surfaces/<version>.json) and
tests/compat/test_surface.py runs it under the CURRENT code. So it may only use what every
release since 0.1.3 has: `ddflow.surfaces.cli.build_parser`, the MCP tool table
(`ddflow.surfaces.tools.TOOLS`, `ddflow.surfaces.mcp.TOOLS` before it moved),
`ddflow.config.Config` and `ddflow.core.model.HANDLERS`.

    python tests/compat/surface.py            # prints the surface of the running release

    cli          command path -> {"flags": [...], "positionals": [[name, required], ...]}
                 (every name a sub-command answers to, aliases included; -h/--help left out)
    tools        MCP tool -> {argument: [type, required]}
    knobs        "section.knob" -> default
    event_kinds  every kind the fold interprets
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import Any


def _cli() -> dict[str, Any]:
    from ddflow.surfaces.cli import build_parser

    found: dict[str, Any] = {}

    def walk(parser: argparse.ArgumentParser, path: str) -> None:
        flags: set[str] = set()
        positionals: list[str] = []
        subs: list[tuple[str, argparse.ArgumentParser]] = []
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                subs.extend(action.choices.items())
            elif action.option_strings:
                flags.update(s for s in action.option_strings if s not in ("-h", "--help"))
            else:
                positionals.append([action.dest, bool(action.required)])
        found[path] = {"flags": sorted(flags), "positionals": positionals}
        for name, sub in subs:
            walk(sub, f"{path} {name}")

    walk(build_parser(), "ddflow")
    return found


def _tools() -> dict[str, Any]:
    try:
        from ddflow.surfaces.tools import TOOLS
    except ImportError:
        from ddflow.surfaces.mcp import TOOLS
    return {
        name: {arg: [spec[0], bool(spec[2])] for arg, spec in (t.get("properties") or {}).items()}
        for name, t in TOOLS.items()
    }


def _knobs() -> dict[str, Any]:
    from ddflow import config as C

    cfg = C.Config()
    skip = set(getattr(C, "_NOT_SECTIONS", ()))
    knobs: dict[str, Any] = {}
    for f in dataclasses.fields(cfg):
        sec = getattr(cfg, f.name)
        if f.name in skip or not dataclasses.is_dataclass(sec):
            continue
        for g in dataclasses.fields(sec):
            knobs[f"{f.name}.{g.name}"] = json.loads(json.dumps(getattr(sec, g.name), default=str))
    return knobs


def surface() -> dict[str, Any]:
    import ddflow
    from ddflow.core.model import HANDLERS

    return {
        "version": ddflow.__version__,
        "cli": _cli(),
        "tools": _tools(),
        "knobs": _knobs(),
        "event_kinds": sorted(HANDLERS),
    }


if __name__ == "__main__":
    json.dump(surface(), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
