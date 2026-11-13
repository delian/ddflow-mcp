"""B054959b3c4: a gate run whose outcome could not be recorded because the event-log lock
timed out died with a raw ``TimeoutError`` traceback, losing a detached ci run. The run
neither retried nor recorded unavailable. It now records ``unavailable`` (retrying against a
transient lock holder) and returns a clean outcome instead of raising."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.api.gates import _run
from ddflow.core import outcome as O


class _Item:
    gates: dict = {}


class _State:
    def __init__(self) -> None:
        self.items = {"T1": _Item()}


def _timing_out(*_a, **_k):
    raise TimeoutError("could not acquire /repo/.ddflow/events.lock within 30s (this is pid 1)")


def test_a_run_whose_record_times_out_returns_unavailable_not_a_traceback(monkeypatch, tmp_path):
    records: list[str] = []

    monkeypatch.setattr(_run, "_execute", _timing_out)
    monkeypatch.setattr(
        _run,
        "_load",
        lambda repo, agent: (None, None, _State()),
    )
    monkeypatch.setattr(
        _run.R,
        "record_measured",
        lambda log, cfg, repo, it, gate, outcome, **k: records.append(outcome),
    )

    out = _run._guarded(
        None, None, tmp_path, "a1", None, "T1", "ci",
        gdef=None, gates={}, cwd=tmp_path, scope=None, forced=False,
        rerun_failed=False, started_on="", started_at="2026-01-01T00:00:00Z", marker=None,
    )

    assert isinstance(out, O.Outcome), repr(out)
    assert out.data["outcome"] == "unavailable"
    assert records == ["unavailable"], records


def test_recording_is_retried_until_the_lock_frees(monkeypatch):
    calls: list[str] = []

    def flaky(log, cfg, repo, it, gate, outcome, **k):
        calls.append(outcome)
        if len(calls) == 1:
            raise TimeoutError("still locked")

    monkeypatch.setattr(_run.R, "record_measured", flaky)
    monkeypatch.setattr(_run.time, "sleep", lambda s: None)

    recorded = _run._record_unavailable_reliably(None, None, None, None, "ci", "r", {}, {}, False)
    assert recorded is True
    assert calls == ["unavailable", "unavailable"], calls


def test_recording_gives_up_cleanly_after_the_retries(monkeypatch):
    def always_locked(log, cfg, repo, it, gate, outcome, **k):
        raise TimeoutError("still locked")

    monkeypatch.setattr(_run.R, "record_measured", always_locked)
    monkeypatch.setattr(_run.time, "sleep", lambda s: None)

    assert (
        _run._record_unavailable_reliably(None, None, None, None, "ci", "r", {}, {}, False)
        is False
    )
