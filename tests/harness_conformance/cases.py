"""What the conformance suite is parameterised over: every shipped descriptor, and the
recorded payload fixtures under tests/fixtures/harness/<id>/<event>.json."""

from __future__ import annotations

import json
from pathlib import Path

from ddflow.services import harnessreg as HR
from ddflow.services import hookwriters as HW

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "harness"

ALL = [h.id for h in HR.load()]
#: Agents whose hooks ddflow writes (a writer exists for the descriptor's style).
WRITABLE = [h.id for h in HR.load() if HW.specs(h)]
#: Agents that command-hook ddflow (they have a normalizer) and so need payload fixtures.
COMMAND_HOOKED = [
    h.id for h in HR.load() if h.hooks is not None and h.hooks.style in HR.COMMAND_HOOK_STYLES
]


def fixtures(harness: str) -> list[tuple[str, dict]]:
    """``(event, fixture)`` for each recorded payload of ``harness``."""
    out = []
    for p in sorted((FIXTURES / harness).glob("*.json")):
        out.append((p.stem, json.loads(p.read_text())))
    return out


def all_fixtures() -> list[tuple[str, str, dict]]:
    return [(h, ev, fx) for h in ALL for ev, fx in fixtures(h)]
