"""What `complete` judges beside the verdict: the phase suite, failed reviews, excused gates, refuted passes, settled umbrellas.

Part of `ddflow.api.lifecycle.complete`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ....core.model import ABANDONED, DONE, State
from ....infra import worktree as W
from ....services import ci as CI
from ....services import gates as G
from ....services import phase_suite as PS
from ....services import revalidate as RV
from ....services.gates import failed_review as GO


def _stands(repo: Path, cfg, log):
    """The `PS.missing` hook: an older full pass whose commit main has moved past still counts when
    the delta reaches no test (SV-revalidate-delta); the revalidation is recorded as a pass."""

    def stands(sha: str) -> str:
        head = W.rev(repo, "HEAD")
        v = RV.assess(repo, sha, head, cfg)
        if v.kind != RV.STANDS:
            return ""
        log.append(
            "cadence.ran",
            PS.CADENCE,
            {"result": "0", "evidence": {"note": v.note, "sha": head, "from": sha}},
        )
        return v.note

    return stands


def _phase_suite(repo: Path, cfg, log, st: State, it, agent: str) -> str:
    """ "" when the full suite has passed on the phase's merged tree (D-gate-economy-r3),
    else the blocker. [schedule].phase_full_suite = run runs `ci run --full` first."""
    phase = it.id
    if it.kind != "phase" or cfg.schedule.phase_full_suite == "off":
        return ""
    why = PS.missing(log.read_all(), st, phase, stands=_stands(repo, cfg, log))
    if not why or cfg.schedule.phase_full_suite != "run":
        return why
    res = CI.run(repo, cfg, ref="HEAD", full=True)
    if res.ok:
        log.append("cadence.ran", PS.CADENCE, {"result": "0", "evidence": {"note": "phase close"}})
        return ""
    return f"the full test suite run for {phase} did not pass: {res.reason}"


def _failed_review(repo: Path, cfg, st: State, item: str, v, umbrella) -> list[str]:
    """Add the failed-review blockers (D-failed-review-gate, B85841c0846) to ``v`` and return
    them. Not lifted by `--force`: the operator's `gate skip --reason` is the recorded way past."""
    if umbrella:
        return []
    found = GO.failed_review_blockers(st, cfg, item, G.load_gates(repo, cfg))
    v.blockers += found
    return found


def _na_map(evidence: list[str]) -> dict[str, str]:
    """`Verdict.na_evidence` ("gate: why" lines) as {gate: why}; a gate id holds no ': '."""
    return dict(line.split(": ", 1) if ": " in line else (line, "") for line in evidence)


def _refuted_extra(st: State, item: str) -> dict[str, Any]:
    """D-unify 5: a gate passed on refutation is never silent in the completion result."""
    flagged = G.refuted_passes(st, [item])
    return {"refuted_passes": [G.refuted_line(r) for r in flagged]} if flagged else {}


def _umbrella_children(st: State, it) -> list[str]:
    """The done sub-tasks of a SETTLED umbrella, or [] when ``it`` is not one: a task split
    into sub-tasks, every one settled and at least one done.

    Its work is its sub-tasks', each of which ran its own pipeline (B651a63e574). All of
    them abandoned is not the work finished, and a phase keeps its own close."""
    if not it.is_work or it.state in (DONE, ABANDONED):
        return []
    below = sorted(st.descendants(it.id))
    if not below or st.open_descendants(it.id):
        return []
    return [i for i in below if st.items[i].state == DONE]
