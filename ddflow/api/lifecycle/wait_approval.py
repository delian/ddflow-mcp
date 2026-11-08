"""`wait --approval`: block until an operator-approval request is answered (DX-wait-approval).

Kept out of `wait.py` so that module does not grow back toward the monolith
(`tests/test_lifecycle_layout.py` counts it under 600 lines): the approval wait is its own
verdict and its own polling loop, sharing `wait`'s `_bounded` and `_keep_alive` (imported
lazily -- this module is imported BY `wait`, so a module import here would cycle).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...core.clock import epoch as _epoch
from ...core.handlers.asks import WITHDRAWN, asks_of
from ...services import waits as WT
from ...services.ask import engine as E
from .._base import _load


def _judge_approval(st, ask_id: str, now: float) -> dict[str, Any]:
    """`wait --approval`'s verdict: the one thing another agent's progress CAN end.

    Pending means waiting can help (the person may answer): blocked. Answered and approved
    means resume the gated command: ready. Denied, withdrawn, expired or unknown is what no
    further waiting ends -- the wait is refused up front with the move that WOULD help.
    """
    out: dict[str, Any] = {"why": "", "waiting_on": [], "ready": [], "blocked": []}
    row = asks_of(st).get(ask_id)
    if row is None:
        return {**out, "status": "hopeless", "why": f"no such approval request {ask_id}"}
    state = E.status(row, now)
    if state == E.ANSWERED:
        if row["type"] != E.APPROVAL:
            return {**out, "status": "hopeless", "why": f"{ask_id} is not an approval request"}
        if row["outcome"] != E.APPROVED:
            return {
                **out,
                "status": "hopeless",
                "why": f"{ask_id} was denied by {row['answered_by']}",
            }
        if row["uses"] and row["used"] >= row["uses"]:
            return {
                **out,
                "status": "hopeless",
                "why": f"{ask_id} is spent ({row['used']} of {row['uses']} use(s))",
            }
        answered = _epoch(row["answered_at"], default=0.0)
        if row["use_ttl_s"] > 0 and now > answered + row["use_ttl_s"]:
            return {
                **out,
                "status": "hopeless",
                "why": (
                    f"{ask_id} was answered more than {row['use_ttl_s']}s ago: "
                    "request a new approval"
                ),
            }
        return {
            **out,
            "status": "ready",
            "ready": [ask_id],
            "why": f"{ask_id} is answered: re-run the gated command with --approval {ask_id}",
        }
    if state == WITHDRAWN:
        return {**out, "status": "hopeless", "why": f"{ask_id} was withdrawn"}
    if state == E.EXPIRED:
        return {**out, "status": "hopeless", "why": f"{ask_id} expired: file a new request"}
    return {
        **out,
        "status": "blocked",
        "why": f"{ask_id} is pending: the operator has not answered it yet",
        "waiting_on": [ask_id],
    }


def _approval_advice(v: dict[str, Any], ask_id: str) -> str:
    """What to do on an approval wait's end: resume the gated command, or stop. Nothing to
    claim -- the lane already holds its item and only needs the answer it asked for."""
    if v["status"] == "ready":
        return f"{ask_id} answered: re-run the gated command with --approval {ask_id}"
    if v["status"] == "hopeless":
        return f"not waiting: {v['why']}"
    return f"still pending: run `ddflow wait --approval {ask_id}` again to keep waiting"


def _approval_outcome(
    v: dict[str, Any], data: dict[str, Any], waited: float, ask_id: str
) -> O.Outcome:
    """The Outcome of an approval wait: ready (exit 0), hopeless (exit 2, waiting cannot
    help), or still pending at the deadline (exit 2, naming the wait that would resume)."""
    if v["status"] == "ready":
        return O.ok("wait", **data)
    if v["status"] == "hopeless":
        return O.nothing("wait", f"Not waiting: {v['why']}", **data)
    return O.nothing(
        "wait",
        f"Still pending after {round(waited)}s: {v['why']} Run `ddflow wait --approval "
        f"{ask_id}` again to keep waiting.",
        **data,
    )


def _wait_approval(  # noqa: PLR0913, PLR0917 -- the facets `wait` forwards
    repo: Path,
    approval: str,
    item: str,
    phase: str,
    lease: str,
    globs,
    token: str,
    timeout_s: float | None,
    poll_s: float | None,
    max_s: float | None,
    agent: str,
    on_progress,
) -> O.Outcome:
    """Block the caller (a lane) until ``approval`` is answered, renewing its own leases all
    the while. Ready when the answer is ``approved`` (re-run the gated command with
    ``--approval ID`` and spend it); refused up front -- and never slept on -- when it is
    denied, withdrawn, expired or unknown, or when the deadline passes still pending.
    """

    def empty_v(v: dict[str, Any]) -> dict[str, Any]:
        return {
            "item": "",
            "phase": "",
            "approval": approval,
            "woke": v["status"] == "ready",
            "waitable": v["status"] != "hopeless",
            "ready": v["ready"],
            "waiting_on": v["waiting_on"],
            "freed_by": [],
            "blocked": v["blocked"],
            "waited_s": 0,
            "holders": [],
        }

    if item or phase or lease or globs or token:
        return O.failed(
            "wait",
            "approval is what to wait on instead of an item, phase, globs, lease or "
            "token: give one",
            **empty_v({"status": "blocked", "ready": [], "waiting_on": [], "blocked": []}),
        )
    from .wait import (
        _bounded,
        _keep_alive,
    )  # lazy: that module imports THIS, so a module import would cycle

    log, cfg, st = _load(repo, agent)
    timeout, _capped = _bounded(timeout_s, max_s, cfg.lease.wait_cap_s)
    poll = WT.POLL_S if poll_s is None else float(poll_s)
    if timeout < 0 or poll <= 0:
        return O.failed(
            "wait",
            "timeout and max-s must be >= 0 and poll > 0 seconds",
            **empty_v({"status": "blocked", "ready": [], "waiting_on": [], "blocked": []}),
        )
    say = on_progress or (lambda _msg: None)

    def result(v: dict[str, Any], waited: float, freed: list[str]) -> O.Outcome:
        data = empty_v(v)
        data.update(
            waited_s=round(waited),
            freed_by=freed,
            wake={"reason": v["status"], "token": "", "messages": []},
            advice=_approval_advice(v, approval),
        )
        return _approval_outcome(v, data, waited, approval)

    v = _judge_approval(st, approval, time.time())
    if v["status"] != "blocked" or timeout == 0:
        return result(v, 0.0, [])

    started = time.monotonic()
    deadline = started + timeout
    say(f"waiting (up to {round(timeout)}s): {v['why']}")
    # The lane's OWN leases stay alive while it waits on its approval (the same keepalive
    # the item-wait uses): hours of waiting must not lapse the very claim the answer is for.
    keep_alive = _keep_alive(log, cfg, st)
    seen = None  # an EventLog.mark() once taken
    checked = started
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return result(v, time.monotonic() - started, [])
            time.sleep(min(poll, left))
            keep_alive()
            ext = log.mark(clock=False)
            if ext == seen and time.monotonic() - checked < WT.RECHECK_S:
                continue
            seen, checked = ext, time.monotonic()
            log, cfg, st = _load(repo, agent)
            was = v
            v = _judge_approval(st, approval, time.time())
            if v["status"] != "blocked":
                return result(v, time.monotonic() - started, [f"{approval}: {v['status']}"])
            if v["why"] != was["why"]:
                say(f"still waiting: {v['why']}")
    finally:
        # No waiter registration for an approval: nothing to mark woken or unregister.
        pass
