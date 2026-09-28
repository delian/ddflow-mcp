"""Two doctor findings whose wording sent the operator the wrong way.

Bug Bf84cccbaa6: `duplicate_work` said items declaring exactly the same files were
"probably a re-description of another". B171 (hotfix back-merge tracking), B176 (review
threads as data) and B178 (rebase-merge landing range) are three distinct features that
merely share coarse imported globs. Identical globs is evidence of a CONFLICT -- they
cannot run at once -- and says nothing about whether the work is the same.

Bug B5189cc5756: for a phase whose tasks are all done, the remedy was
`ddflow complete <phase>`, which exits 3 while the phase's OWN gates are unrecorded. A
remedy that cannot succeed is worse than none: the operator runs it, it refuses, and the
finding is still there. The remedy is therefore decided by `completion.verdict()` -- the
one place that decides completion -- and the doctor tests drive `doctor` itself: a
re-derived rule missed a FAILED required gate (every gate had an outcome, so "complete"
was offered) and `require_outcome = false` (silence does not block, yet the remedy said
to satisfy the gates first).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import reporting
from ddflow.config import Config
from ddflow.core import progress as PR
from ddflow.core.model import fold
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

BARE = "`ddflow complete P`, or file"


def _finished_phase(repo, outcomes: dict[str, str] | str = "", config: str = "") -> str:
    """The doctor line for phase P, whose only task is done.

    `outcomes` maps gate -> event kind; "*" records every phase gate as passed.
    """
    assert run_cli(repo, "init")[0] == 0
    if config:
        with (repo / ".ddflow" / "config.toml").open("a", encoding="utf-8") as f:
            f.write("\n" + config)
    pipeline = list(Config.load(repo).gates.phase_pipeline)
    log = EventLog(repo, "a1")
    log.append("phase.added", "P", {"title": "done but open", "kind": "phase"})
    log.append("task.added", "P.T1", {"title": "t", "kind": "task", "parent": "P"})
    log.append("item.completed", "P.T1", {})
    recs = dict.fromkeys(pipeline, "gate.passed") if outcomes == "*" else dict(outcomes or {})
    for g, kind in recs.items():
        log.append(kind, "P", {"gate": g, "kind": "phase"})
    out = reporting.doctor(repo, agent="a1")
    [line] = [x for x in out.data["problems"] if x.startswith("P: ")]
    assert "task(s) under it are finished" in line, line
    return line


def test_a_finished_phase_with_unrecorded_gates_names_them(repo):
    detail = _finished_phase(repo)
    pipeline = list(Config.load(repo).gates.phase_pipeline)
    assert pipeline, "the default phase pipeline is not empty"
    assert BARE not in detail, detail
    assert "would refuse" in detail and "`ddflow gate status P`" in detail, detail
    for g in pipeline:
        assert g in detail, (g, detail)


def test_a_finished_phase_whose_gates_all_ran_is_told_to_complete(repo):
    detail = _finished_phase(repo, "*")
    assert BARE in detail, detail
    assert "gate status" not in detail, detail


def test_a_failed_required_gate_is_not_answered_with_complete(repo):
    """Every gate has an outcome, but a REQUIRED one failed: `complete` refuses."""
    pipeline = list(Config.load(repo).gates.phase_pipeline)
    assert "unit_tests" in pipeline and "unit_tests" in Config.load(repo).gates.required
    recs = dict.fromkeys(pipeline, "gate.passed")
    recs["unit_tests"] = "gate.failed"
    detail = _finished_phase(repo, recs)
    assert BARE not in detail, detail
    assert "would refuse" in detail and "unit_tests" in detail, detail
    assert "required gate(s) not passed" in detail, detail
    assert "`ddflow gate status P`" in detail, detail


def test_silence_that_does_not_block_is_not_reported_as_blocking(repo):
    """require_outcome = false and nothing required: silent gates do not stop `complete`,
    so the remedy is to complete, not to satisfy gates first."""
    detail = _finished_phase(repo, config="[gates]\nrequire_outcome = false\nrequired = []\n")
    assert BARE in detail, detail
    assert "gate status" not in detail and "would refuse" not in detail, detail


def test_every_blocker_is_quoted_whole(repo):
    """Rubber-duck on f293315: cutting each blocker at its first `. ` truncated a gate
    named `review. final` to `review` -- a gate that does not exist -- and lost every gate
    named after it. The verdict's text is quoted as written, not parsed."""
    detail = _finished_phase(
        repo,
        config='[gates]\nphase_pipeline = ["research", "review. final", "merge"]\n'
        'required = ["review. final"]\n',
    )
    assert "review. final" in detail, detail
    assert "merge" in detail.split("would refuse", 1)[1], detail
