"""A required or merge-blocking gate that FAILED stays failed (DX-skip-failed-gate,
B23bd81462d): a skip over the failure is refused or flagged, and merge refuses a failed ci."""

from __future__ import annotations

from collections.abc import Iterable

from ...config import Config
from ...core.model import State


def skipped_after_failure(it, gate: str) -> bool:
    """Is ``gate``'s latest outcome a skip that replaced a FAILED run (DX-skip-failed-gate)?
    Skips between the failure and now do not clear it; any other outcome does."""
    if it.gate_outcome(gate) != "skipped":
        return False
    for run in reversed(it.runs_of(gate)):
        if run.outcome != "skipped":
            return run.outcome == "failed"
    return False


def failed_ci_blockers(state: State, cfg: Config, item_id: str) -> list[str]:
    """Blockers for each `[gates].merge_blocked_by_failed` gate (default ci) that is FAILED
    or skipped over a failure: landing that is main going red (B23bd81462d). The way past is
    a run that passes."""
    it = state.items.get(item_id)
    if it is None:
        return []
    out = []
    for gate in cfg.gates.merge_blocked_by_failed:
        if it.gate_outcome(gate) == "failed":
            what = "failed"
        elif skipped_after_failure(it, gate):
            what = "was skipped after a failed run"
        else:
            continue
        out.append(
            f"{gate} {what}; landing it can turn the base red. Fix it and re-run "
            f"`ddflow gate run {item_id} {gate}` so it passes, or "
            f"record the real state: `ddflow gate record {item_id} {gate} --outcome "
            f"unavailable|partial --reason ...`."
        )
    return out


def skip_over_failure_refusal(it, gate: str, item: str, required: Iterable[str]) -> str:
    """Why skipping ``gate`` is refused ("" when it is allowed): a REQUIRED gate whose latest
    outcome is failed is not a skip, it is a failure; record `unavailable` or `partial` with
    the reason, or fix and re-run."""
    if gate not in required or it.gate_outcome(gate) != "failed":
        return ""
    return (
        f"{gate} is a required gate and its latest outcome is FAILED; that is not skippable. "
        f"Fix it and re-run `ddflow gate run {item} {gate}`, or record the real state: "
        f"`ddflow gate record {item} {gate} --outcome unavailable|partial --reason ...`."
    )
