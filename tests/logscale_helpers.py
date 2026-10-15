"""Shared fixtures of the log-scale tests (B-gap-log-scale): one generated log, and a COPY of
this repository's own log when it has one (never the working tree in place)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ddflow.infra.log import EventLog
from ddflow.services import logfixture

REAL = Path(__file__).resolve().parents[1] / ".ddflow" / "events"


@pytest.fixture(scope="module")
def _real_copy(tmp_path_factory) -> Path | None:
    if not REAL.is_dir():
        return None
    root = tmp_path_factory.mktemp("reallog")
    shutil.copytree(REAL, root / ".ddflow" / "events")
    return root


@pytest.fixture(params=["generated", "real"])
def any_log(request, tmp_path, _real_copy) -> EventLog:
    """An event log to prove an equivalence on: generated, then this repository's own."""
    if request.param == "generated":
        logfixture.generate(tmp_path / ".ddflow" / "events", 4000, seed=3)
        return EventLog(tmp_path)
    if _real_copy is None:
        pytest.skip("this checkout has no .ddflow/events to copy")
    return EventLog(_real_copy)
