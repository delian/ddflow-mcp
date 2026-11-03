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


def _pkg_py_files(pkg: Path) -> list[Path]:
    """The package ``*.py`` files a ratchet actually scans, in a stable order.

    Skip a bytecode-cache DIRECTORY by its component name, not by substring. The loop
    once matched ``"__pycache__" in str(f)`` on the WHOLE path, so a checkout living under
    a directory whose name merely contains ``__pycache__`` skipped every file and the
    ratchet scanned nothing. ``*.py`` never lives inside a real ``__pycache__`` (those
    hold ``*.pyc``), but matching by component keeps the guard honest if one ever does.
    """
    return sorted(f for f in pkg.rglob("*.py") if "__pycache__" not in f.relative_to(pkg).parts)


def _ids(events) -> list[str]:
    return [e.id or e.compute_id() for e in events]
