"""What a descriptor DECLARES, and so what it owes a fixture for.

A hook event it maps owes a payload fixture (E1/E2); a ``[headless]`` launch template owes an
argv snapshot (E4) and, when it names an output format, a stream recording (E5, E6, and the
Lane contract run against a fake agent that replays it). Derived from the descriptor alone, so a
new ``ddflow/harnesses/<id>.toml`` is checked with no edit here.
"""

from __future__ import annotations

import json
from pathlib import Path

from ddflow.services import harnessreg as HR

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "harness"

#: Check kinds. ``hook`` is one per mapped event; ``argv`` and ``stream`` are per agent.
HOOK, ARGV, STREAM = "hook", "argv", "stream"
#: Fixture status. ``unverified`` is a gap, never a pass; ``synthetic`` is honest, not verified.
VERIFIED, SYNTHETIC, UNVERIFIED = "verified", "synthetic", "unverified"


def headless(h):
    hl = h.headless
    return hl if hl is not None and not hl.none and hl.argv else None


def declared(h) -> list[tuple[str, str]]:
    """``(kind, name)`` pairs the descriptor owes a fixture for."""
    out: list[tuple[str, str]] = []
    if h.hooks is not None and h.hooks.style in HR.COMMAND_HOOK_STYLES:
        out += [(HOOK, ev) for ev in sorted(h.hooks.events)]
    hl = headless(h)
    if hl is not None:
        out.append((ARGV, ""))
        if hl.output_flag or hl.output_format:
            out.append((STREAM, ""))
    return out


def path_for(hid: str, kind: str, name: str = "") -> Path:
    if kind == HOOK:
        return FIXTURES / hid / f"{name}.json"
    return FIXTURES / hid / "engines" / f"{kind}.json"


def load(hid: str, kind: str, name: str = "") -> dict | None:
    p = path_for(hid, kind, name)
    return json.loads(p.read_text()) if p.is_file() else None


def status(fx: dict | None) -> str:
    if fx is None:
        return UNVERIFIED
    return VERIFIED if fx["_meta"].get("recorded") is True else SYNTHETIC


def all_checks() -> list[tuple[str, str, str]]:
    return [(h.id, k, n) for h in HR.load() for k, n in declared(h)]
