"""Bug B0dcc0ba801: the backlog migration closed the wrong item, and the log says so.

`docs/BACKLOG.md@6be308a:284-300` put a "CLOSED -- the cross-family critic ran on every
pass since" paragraph under B14 (multi-machine clock skew, THEORETICAL, never probed)
though it describes B12 (cross-family review). The migration completed B14 on it and left
B12 open. The correction is DATA, made with ordinary events and never by editing history:
B14 blocked with its THEORETICAL reason (as the migration left its sibling B13), B12
completed. This guards this repository's own log against losing that correction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.core.model import DONE, fold
from ddflow.infra.log import EventLog

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_EVIDENCE = "marked closed in docs/BACKLOG.md:291"


@pytest.fixture(scope="module")
def items():
    if not (ROOT / ".ddflow" / "events").is_dir():
        pytest.skip("no event log: not a ddflow checkout (an sdist, say)")
    return fold(EventLog(ROOT).read_all(), strict=False).items


def test_b14_is_not_closed_on_the_paragraph_that_describes_b12(items):
    b14 = items["B14"]
    assert not (b14.state == DONE and MIGRATION_EVIDENCE in (b14.completion_evidence or "")), (
        b14.state,
        b14.completion_evidence,
    )


def test_b12_the_item_that_paragraph_describes_is_closed(items):
    assert items["B12"].state == DONE, items["B12"].state
