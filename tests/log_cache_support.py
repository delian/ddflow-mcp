"""Shared fixtures and helpers for the tests/test_log_read_cache_*.py modules.

The two autouse fixtures are imported into each module (a fixture only applies in the module
whose namespace holds it)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.config import LogConfig
from ddflow.infra.log import EventLog, clear_parse_cache


@pytest.fixture(autouse=True)
def _no_version_stamp(monkeypatch):
    """These tests are about the log's storage mechanics (event counts, byte offsets, clock
    values); the version stamp (B-upgrade.1-stamp) is tested in tests/test_upgrade_stamp.py."""
    monkeypatch.setattr(EventLog, "stamp", False)


@pytest.fixture(autouse=True)
def _cold_cache():
    """Every test starts with nothing parsed. A cache shared between tests is a test
    that passes because of its neighbour."""
    clear_parse_cache()
    yield
    clear_parse_cache()


def _uncached(repo, agent="a1") -> EventLog:
    return EventLog(repo, agent, log_cfg=LogConfig(reuse_parsed=False))


def _ids(events) -> list[str]:
    return [e.id or e.compute_id() for e in events]
