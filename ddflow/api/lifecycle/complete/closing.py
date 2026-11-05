"""The bug-closing and phase-end halves of `complete`: the close-argument refusals, `bug_fixed` per pending bug, the self-assessment pass.

Part of `ddflow.api.lifecycle.complete`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ....core import outcome as O
from ...knowledge import bug_fixed
from ...knowledge.regression import regression_arg_refusal
from ...knowledge.self_assessment import auto_file


def _phase_end_self_assessment(repo: Path, text: str, agent: str, extra: dict[str, Any]) -> None:
    """The self-assessment pass at phase end (r-ddflow-self-assessment, default on): file each
    defect this phase's completion names (`--changelog`), skipping duplicates; record its
    verdict in `extra` when it filed, skipped or failed anything. Never raises: the
    completion that ran the pass returns whatever it would have, filed defects or not."""
    try:
        assessed = auto_file(repo, text=text, trigger="phase_end", agent=agent)
        if (
            assessed.data.get("filed")
            or assessed.data.get("skipped")
            or assessed.data.get("failed")
        ):
            extra["self_assessment"] = assessed.data
    except Exception as exc:  # a best-effort pass must not abort phase completion
        extra["self_assessment"] = {
            "filed": [],
            "skipped": [],
            "failed": [f"{type(exc).__name__}: {exc}"],
        }


def _close_arg_refusal(
    repo, item, closing, pending, tests, lint_exempt, regression_test
) -> O.Outcome | None:
    """The `item.completed` refusal for the close args, or None.

    Both halves run BEFORE any approval is spent (B3c1a2491a3). First the pre-existing
    rule — a close flag on an item that fixes no open bug — then the malformed
    `--regression-test` check, which `bug_fixed` only reaches after the override: a file
    path where a pytest node id is required must be refused here, or a typo wastes the
    operator's single use. `--lint-exempt` skips the second half: `bug_fixed` short-circuits
    on `lint_exempt` and never inspects `regression_test`, so the pre-check must not refuse
    what the close would accept.
    """
    if closing and not pending:
        return O.refused(
            "item.completed",
            f"--regression-test/--lint-exempt: {item} is not the fix task of any open bug, so there is "
            f"nothing for it to close -- drop the flag, or close a bug with `ddflow bug "
            f"fixed <bug> --regression-test ...` (--lint-exempt for a CI lint bug).",
            id=item,
            bugs_closed=[],
        )
    if lint_exempt.strip() or not any(t.strip() for t in tests):
        return None
    bad = regression_arg_refusal(repo, regression_test)
    if bad is None:
        return None
    return O.Outcome(
        kind="item.completed",
        data={"id": item, "bugs_closed": []},
        exit=bad.exit,
        reason=bad.reason,
    )


def _close_bugs(
    repo, item, pending, closed, tests, mutation, lint_exempt, agent
) -> O.Outcome | None:
    """Close each pending bug through `bug_fixed` (the same refusals); the failure, else None."""
    flag = (
        "--lint-exempt"
        if lint_exempt.strip() and not any(t.strip() for t in tests)
        else "--regression-test"
    )
    for bid in pending:
        out = bug_fixed(
            repo,
            bid,
            regression_test=tests,
            mutation=mutation,
            lint_exempt=lint_exempt.strip(),
            agent=agent,
        )
        if out.exit != O.OK:
            return O.Outcome(
                kind="item.completed",
                data={"id": item, "bug": bid, "bugs_closed": closed},
                exit=out.exit,
                reason=f"{flag}: bug {bid} not closed: {out.reason}",
            )
        closed.append(bid)
    return None
