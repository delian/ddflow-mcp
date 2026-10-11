"""B-start-timeout-doctor: `ddflow doctor` names an unfinished upgrade start check, and the
brief that already holds the folded state does not fold the log again to say it.

A start check that ran out of time (or failed) leaves a NOTE in the marker. The brief repeats it
and doctor must too, with the exact remedy, until the project is upgraded; a project that is not
behind this ddflow gets no note.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_upgrade_on_start import _load, _seed_old

from ddflow.api import lifecycle, reporting
from ddflow.services import upgrade_plan as UP
from ddflow.services import upgrade_start as US

CHECK = {US.ENV: "check"}
REMEDY = "ddflow upgrade --plan"


@pytest.fixture
def old(tmp_path: Path) -> Path:
    return _seed_old(tmp_path)


def _timed_out(old: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    real = UP.build

    def slow(*a, **k):  # the existing seam: the plan, made slower than the timeout
        time.sleep(0.6)
        return real(*a, **k)

    monkeypatch.setattr(UP, "build", slow)
    log, cfg, st = _load(old, "starter")
    cfg.upgrade.start_timeout_s = 0.1
    rep = US.start(old, log, cfg, st, surf="mcp", environ=CHECK)
    assert rep["status"] == US.TIMEOUT, rep
    end = time.monotonic() + 10  # the left-running check finishes and keeps its answer ...
    while US._read(old).get("status") != US.PROPOSED and time.monotonic() < end:
        time.sleep(0.05)
    assert US._read(old).get("status") == US.PROPOSED
    # ... so put the timeout note back: the state under test is a marker that holds the note
    note = {"version": US.running_version(), "status": US.TIMEOUT, "text": rep["text"]}
    (old / US.MARKER).write_text(json.dumps(note) + "\n", "utf-8")
    return rep["text"]


def _doctor_notes(repo: Path) -> list[str]:
    return list(reporting.doctor(repo, agent="a1").data["notes"])


def test_doctor_shows_the_note_of_a_timed_out_start_check_with_its_remedy(old, monkeypatch) -> None:
    text = _timed_out(old, monkeypatch)
    assert REMEDY in text
    assert text in _doctor_notes(old)


def test_a_project_that_is_not_behind_gets_no_such_note(old, monkeypatch) -> None:
    text = _timed_out(old, monkeypatch)
    log, _cfg, _st = _load(old, "starter")
    log.append("upgrade.applied", "", {"to": US.running_version()})
    assert text not in _doctor_notes(old)


def test_the_brief_does_not_fold_the_log_again_while_the_note_repeats(old, monkeypatch) -> None:
    text = _timed_out(old, monkeypatch)
    assert US.doctor_notes(old, _load(old, "starter")[2]), "the note repeats"
    folds: list[Path] = []
    real = US._state_of

    def counted(repo: Path):
        folds.append(repo)
        return real(repo)

    monkeypatch.setattr(US, "_state_of", counted)
    for _ in range(2):
        out = lifecycle.brief(old, agent="a1")
        assert text in out.data["text"], "the note keeps repeating in the brief"
    assert folds == [], "the brief already holds the state: it must hand it over"
