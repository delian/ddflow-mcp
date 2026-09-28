"""Two doctor findings whose wording sent the operator the wrong way.

Bug Bf84cccbaa6: `duplicate_work` said items declaring exactly the same files were
"probably a re-description of another". B171 (hotfix back-merge tracking), B176 (review
threads as data) and B178 (rebase-merge landing range) are three distinct features that
merely share coarse imported globs. Identical globs is evidence of a CONFLICT -- they
cannot run at once -- and says nothing about whether the work is the same.

Bug B5189cc5756: for a phase whose tasks are all done, the remedy was
`ddflow complete <phase>`, which exits 3 while the phase's OWN gates are unrecorded. A
remedy that cannot succeed is worse than none: the operator runs it, it refuses, and the
finding is still there.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import progress as PR
from ddflow.core.model import fold
from ddflow.core.schedule import unpickable
from ddflow.infra.log import EventLog

# -- Bf84cccbaa6: identical globs is a conflict, not a duplicate -------------------------


def _dupes(repo) -> list[str]:
    log = EventLog(repo, "a1")
    evs = log.read_all()
    st = fold(evs, strict=False)
    return [f.detail for f in PR.detect(evs, st, Config.load(repo)) if f.kind == "duplicate_work"]


def test_shared_globs_are_not_called_a_re_description(repo):
    assert run_cli(repo, "init")[0] == 0
    log = EventLog(repo, "a1")
    for tid, title in (("A", "hotfix back-merge tracking"), ("B", "review threads as data")):
        log.append("task.added", tid, {"title": title, "kind": "task", "globs": ["forge.py"]})
    [detail] = _dupes(repo)
    assert "re-description" not in detail, detail
    assert "cannot run" in detail and "in parallel" in detail, detail
    assert "forge.py" in detail and "A, B" in detail, detail


# -- B5189cc5756: the remedy for a finished phase must be one that can succeed ----------


def _finished_phase(repo, *gates_recorded: str) -> tuple[str, list[str]]:
    assert run_cli(repo, "init")[0] == 0
    pipeline = list(Config.load(repo).gates.phase_pipeline)
    log = EventLog(repo, "a1")
    log.append("phase.added", "P", {"title": "done but open", "kind": "phase"})
    log.append("task.added", "P.T1", {"title": "t", "kind": "task", "parent": "P"})
    log.append("item.completed", "P.T1", {})
    for g in pipeline if gates_recorded == ("*",) else gates_recorded:
        log.append("gate.passed", "P", {"gate": g, "kind": "phase"})
    st = fold(log.read_all(), strict=False)
    [u] = [u for u in unpickable(st, Config.load(repo)) if u.item == "P"]
    assert u.kind == "finished_phase"
    return u.detail, pipeline


def _named(detail: str) -> list[str]:
    return detail.split("no outcome yet (", 1)[1].split(")", 1)[0].split(", ")


def test_a_finished_phase_with_unrecorded_gates_names_them(repo):
    detail, pipeline = _finished_phase(repo)
    assert pipeline, "the default phase pipeline is not empty"
    assert _named(detail) == pipeline, detail
    assert "`ddflow gate status P`" in detail, detail


def test_only_the_gates_still_silent_are_named(repo):
    detail, pipeline = _finished_phase(repo, "research")
    assert _named(detail) == [g for g in pipeline if g != "research"], detail


def test_a_finished_phase_whose_gates_all_ran_is_told_to_complete(repo):
    detail, _pipeline = _finished_phase(repo, "*")
    assert "`ddflow complete P`" in detail, detail
    assert "gate status" not in detail, detail
