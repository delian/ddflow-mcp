"""`watch`: wait for a job, a file line, a process or a gate while the caller's leases are
renewed; see `ddflow.services.watch`."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.defaults import DEFAULT_WAIT_TIMEOUT_S
from ..services import inbox as IB
from ..services import jobs as J
from ..services import leasekeep as LK
from ..services import waits as WT
from ..services import watch as W
from ._base import _load


def _items(heartbeat: str | list[str]) -> list[str]:
    parts = heartbeat.split(",") if isinstance(heartbeat, str) else list(heartbeat)
    return [p.strip() for p in parts if p and p.strip()]


def _pacer(log, cfg, st, named: list[str]):
    """(a tick renewing the leases, the named items it could not renew). Named items, else
    every live lease the caller holds. Paced to ``[lease].heartbeat_s``."""
    if not named:
        tick, _every = LK.for_holder(log, cfg, st)
        return tick or (lambda: None), []
    ticks, missed = [], []
    for item in named:
        it = st.items.get(item)
        k = LK.keeper(log, cfg, it) if it is not None else None
        if k is None:
            missed.append(item)
        else:
            ticks.append(LK.throttled(k, it.lease.renewed_at, cfg.lease.heartbeat_s, WT.POLL_S))

    def tick() -> None:
        for t in ticks:
            t()

    return tick, missed


def _probe(t: W.Target, st) -> dict[str, Any] | None:
    """What the target shows now, or None while it is still pending."""
    if t.file:
        line = W.scan_file(t)
        return {"line": line} if line is not None else None
    if t.pid:
        return {} if W.pid_gone(t) else None
    if t.gate:
        out = W.gate_outcome(st, t)
        return {"outcome": out} if out else None
    job = st.jobs.get(t.job)
    if job is None:
        return {"detail": "no such job", "status": "missing"}
    s = J.status(job)
    if s.state in ("running", "elsewhere"):  # elsewhere: not watchable, never "finished"
        return None
    return {"status": s.state, "detail": s.detail, "exit_code": s.exit_code}


def _target(token, job, file, until, pid, gate, heartbeat) -> tuple[W.Target | None, str]:
    """(the target, why not): from a token alone, or from the named facets."""
    if token:
        t = W.decode(token)
        if t is None:
            return None, "that is not a watch token"
        if job or file or until or pid or gate or heartbeat:
            return None, "a token already names its target: give it alone"
    else:
        t = W.Target(
            job=job,
            file=file,
            until=until,
            pid=int(pid or 0),
            gate=gate,
            heartbeat=_items(heartbeat),
        )
    return t, W.problem(t)


def _refuse_elsewhere(st, t: W.Target) -> O.Outcome | None:
    """A job on another host cannot be watched from here: it is refused (exit 1), not waited on."""
    if not t.job:
        return None
    row = st.jobs.get(t.job)
    if row is None:
        return O.failed("watch", f"no such job {t.job!r}")
    if J.status(row).state == "elsewhere":
        return O.failed(
            "watch", f"job {t.job} runs on another host: it cannot be watched from here"
        )
    return None


def _sane(timeout: float, poll: float) -> bool:
    return math.isfinite(timeout) and math.isfinite(poll) and timeout >= 0 and poll > 0


def _report(
    t: W.Target, started: float, missed: list[str], extra: dict[str, Any]
) -> dict[str, Any]:
    """The wire body of a finished or timed-out watch."""
    return {
        "watching": t.kind,
        "target": t.job or t.file or t.gate or t.pid,
        "waited_s": round(time.monotonic() - started),
        "token": W.encode(t),
        "unrenewed": missed,
        **extra,
    }


def watch(  # noqa: PLR0913 -- one keyword per facet of the target and the wait
    repo: Path,
    *,
    job: str = "",
    file: str = "",
    until: str = "",
    pid: int = 0,
    gate: str = "",
    heartbeat: str | list[str] = "",
    timeout_s: float | None = None,
    poll_s: float | None = None,
    token: str = "",
    agent: str = "",
    on_progress=None,
) -> O.Outcome:
    """Wait for the target, renewing the caller's leases, and report what happened.

    Exit 0: it happened (``data`` carries the matching ``line``, a job's ``exit_code`` or a
    gate's ``outcome``). Exit 2: the deadline passed first; ``token`` resumes from where this
    stopped. Exit 1: the target cannot be watched (``reason`` says why). ``token`` alone
    resumes a watch, target and leases included."""
    t, bad = _target(token, job, file, until, pid, gate, heartbeat)
    if bad:
        return O.failed("watch", bad)
    timeout = DEFAULT_WAIT_TIMEOUT_S if timeout_s is None else float(timeout_s)
    poll = WT.POLL_S if poll_s is None else float(poll_s)
    if not _sane(timeout, poll):
        return O.failed(
            "watch", "timeout must be a finite number >= 0 and poll a finite number > 0 seconds"
        )
    log, cfg, st = _load(repo, agent)
    cap = float(cfg.lease.wait_cap_s)
    if timeout > cap:
        (on_progress or (lambda _m: None))(
            f"watching at most {cap:g}s at a time; resume with the token"
        )
    me = cfg.agent.id or log.agent_id
    elsewhere = _refuse_elsewhere(st, t)
    if elsewhere is not None:
        return elsewhere
    renew, missed = _pacer(log, cfg, st, t.heartbeat)
    started = time.monotonic()
    deadline = started + min(timeout, cap)

    def done(extra: dict[str, Any], reason: str = "") -> dict[str, Any]:
        """The wire body, with the wake: why this returned, the resume token, and whatever
        the lane's inbox held (delivered here, fenced)."""
        got = IB.wake(repo, me, ttl_s=cfg.lease.inbox_ttl_s) or {}
        why = reason if reason == "ready" else (got.get("reason") or reason)
        body = _report(t, started, missed, extra)
        body["wake"] = {"reason": why, "token": body["token"], "messages": got.get("messages", [])}
        return body

    seen_mark, checked = None, started
    renew()  # a lease close to expiry is renewed before the first sleep, not after it
    while True:
        found = _probe(t, st)
        if found is not None:
            return O.ok("watch", **done({"matched": True, **found}, "ready"))
        left = deadline - time.monotonic()
        if left <= 0:
            return O.nothing(
                "watch",
                f"Not yet after {round(time.monotonic() - started)}s; `watch` with the token "
                f"goes on from here.",
                **done({"matched": False, "timed_out": True}, "timeout"),
            )
        # Never sleep past a renewal: a long poll would let the lease lapse mid-wait.
        time.sleep(min(poll, left, max(1.0, cfg.lease.heartbeat_s / 2)))
        renew()
        if IB.has_pending(repo, me, ttl_s=cfg.lease.inbox_ttl_s):
            # A message, offer or yield ends the wait early, with the token to go on.
            return O.ok("watch", **done({"matched": False, "timed_out": False}, "message"))
        # Only a gate (or a job whose record may change) is judged on the folded log.
        if t.gate or t.job:
            mark = log.mark(clock=False)
            if mark != seen_mark or time.monotonic() - checked >= WT.RECHECK_S:
                seen_mark, checked = mark, time.monotonic()
                log, cfg, st = _load(repo, agent)
                gone = _refuse_elsewhere(st, t)
                if gone is not None:
                    return gone
                renew, again = _pacer(log, cfg, st, t.heartbeat)
                missed = sorted({*missed, *again})


def watch_summary(d: dict[str, Any]) -> str:
    """One human line for what a finished watch saw."""
    t = W.Target(**{d["watching"]: d["target"]}) if d.get("watching") else W.Target()
    return W.summary(t, d)
