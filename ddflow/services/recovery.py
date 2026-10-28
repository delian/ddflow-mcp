"""Apply the SAFE_AUTO half of a recovery plan, and gather the facts it triages
(DX-recovery-plan, orphan-recovery.md research sections 3.2, 3.6 and 3.7).

``core.recovery_plan`` is the pure brain; this module is the hands. It

* gathers the measured rows the triage consumes (the lease sweep's recoveries, the
  orphaned gate-run markers, the unended jobs, the queue entries left running) --
  reusing the very same measures `doctor` and `scan` read, never a second detector;
* applies only SAFE_AUTO actions, each under the recovery flock, re-checking its own
  precondition immediately before acting (time-of-check gap);
* counts recovery attempts per item (today the fold of `lease.expired`, the honest
  predecessor of the `recovery.resumed` fold `DX-recovery-guards` will add);
* reads the kill switch: with `.ddflow/local/recovery.stop` present, an apply refuses
  (exit 3) instead of running -- the plan keeps printing, a dry run changes nothing.

All the I/O in one place; everything irreversible is an event append with a
deterministic key (`L.expire` and `job.ended` already behave that way: folding twice is
a no-op).
"""

from __future__ import annotations

import contextlib
import importlib
from pathlib import Path

from ..infra import fsio

#: Marker file that stops `recover --apply` from running anything (plan stays dry).
KILL_SWITCH = "recovery.stop"

#: The recovery flock, one pass at a time. The kernel frees it with its holder, so a
#: crashed pass never wedges the next one (the merge-queue integrator lock is the model).
LOCK = "recovery.lock"


def local_dir(repo: Path) -> Path:
    return Path(repo) / ".ddflow" / "local"


def kill_switch_path(repo: Path) -> Path:
    return local_dir(repo) / KILL_SWITCH


def kill_switch(repo: Path, cfg=None) -> bool:
    """Is the emergency brake on? Read per pass: the operator can pull it mid-flight.

    D-recovery-one-design names one knob family `[recover]` (enabled kill switch;
    auto = propose|safe), landed with DX-recovery-guards. Until it exists, the brake is
    the marker file; once the section ships, its `enabled` is the one answer and the
    file waits for file-less projects."""
    rc = getattr(cfg, "recover", None)
    if rc is not None and hasattr(rc, "enabled"):
        return not rc.enabled
    return kill_switch_path(repo).exists()


def attempt_counts(log) -> dict[str, int]:
    """item -> how many times its lease lapsed into recovery so far.

    Work began here because falling back into the expire-to-claim loop is the loop the
    cap exists to break; the `recovery.resumed` event lands with DX-recovery-guards and
    this fold moves with it."""
    out: dict[str, int] = {}
    for e in log.read_all():
        if e.kind == "lease.expired":
            out[e.subject] = out.get(e.subject, 0) + 1
    return out


@contextlib.contextmanager
def pass_lock(repo: Path):
    """Hold the recovery flock or yield False (another pass holds it)."""
    d = local_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(fsio.file_lock(d / LOCK, 0))
        except fsio.LockTimeout:
            yield False
            return
        yield True


def job_rows(st, jobs) -> list[dict]:
    """Unended jobs whose process already made its answer known, as plain rows."""
    out = []
    for j in st.jobs.values():
        if j.ended_at:
            continue
        s = jobs.status(j)
        if s.state in ("exited", "gone"):
            out.append({"id": j.id, "item": j.item, "state": s.state, "exit_code": s.exit_code})
    return out


def apply(repo: Path, log, agent: str, actions, cfg=None) -> list[dict]:
    """Run each SAFE_AUTO action, once, under the flock; PROPOSE/OPERATOR are shown only.

    Returns one row per action: ``applied`` True/False and what was recorded. An action
    whose precondition no longer holds (already expired, job already ended, the queue
    entry moved) is applied=False with its reason, never an error: folding twice is a
    no-op by the doc's design, so re-applying is the expected and safe outcome.

    The kill switch is read again under the flock, immediately before acting: the
    operator can pull the brake after the caller's own pre-check, and the pointer check
    is cheap enough to mean it."""
    from ..services import leases as L
    from ..services import mergequeue as MQ

    rows = []
    with pass_lock(repo) as lock:
        if not lock:
            return [
                {
                    "item": a.item,
                    "applied": False,
                    "note": "another recovery pass holds the flock; nothing ran here",
                }
                for a in actions
            ]
        if kill_switch(repo, cfg):
            return [
                {"item": a.item, "applied": False, "note": "the kill switch is on: nothing ran"}
                for a in actions
            ]
        for a in actions:
            rows.append(_one_safe(repo, log, agent, a, L, MQ))
    return rows


def _one_safe(repo, log, agent, a, L, MQ) -> dict:
    """One action under the flock, its own precondition re-checked immediately before it
    acts. PROPOSE/OPERATOR come back shown-only; an unexpected step reports, never sinks."""
    if a.cls != "safe_auto":
        return {"item": a.item, "applied": False, "note": f"{a.cls} is shown only"}
    if a.evidence.get("effect") == "expire":
        recorded = L.expire(log, a.item, reason="recovery-plan sweep: clean tree")
        return {
            "item": a.item,
            "applied": bool(recorded),
            "note": a.reason if recorded else "an expiry is already recorded",
        }
    if a.evidence.get("effect") == "record_unavailable":
        GA = importlib.import_module("ddflow.api.gates")
        swept = GA.sweep_dead_runs(repo, agent, a.item)
        return {
            "item": a.item,
            "applied": bool(swept),
            "note": f"{a.evidence.get('gate')} unavailable recorded"
            if swept
            else f"{a.evidence.get('gate')}: nothing owed since the run began",
        }
    if a.evidence.get("effect") == "job_end":
        JAPI = importlib.import_module("ddflow.api.jobs")
        out = JAPI.job_end(repo, job=a.evidence.get("job", ""), agent=agent)
        return {"item": a.item, "applied": out.exit == 0, "note": out.reason}
    if a.evidence.get("effect") == "requeue":
        return _requeue(repo, agent, a, MQ)
    return {"item": a.item, "applied": False, "note": f"unknown effect {a.evidence.get('effect')!r}"}


def _requeue(repo, agent, a, MQ) -> dict:
    """THIS entry only: a plan action may never requeue an entry the plan did not name
    (the whole-list requeue is the integrator's own standby, not this pass's)."""
    try:
        with MQ.integrator(repo, agent=agent) as integrator:
            taken: list[str] = []
            if integrator:
                for e in MQ.entries(repo):
                    if e.item == a.item and e.state == MQ.RUNNING:
                        e.state, e.started_at = MQ.QUEUED, 0.0
                        e.reason = "the recovery pass requeued it (integrator gone)"
                        MQ._write(repo, e)
                        taken.append(e.item)
        return {
            "item": a.item,
            "applied": bool(taken),
            "note": f"{a.item} requeued"
            if taken
            else f"{a.item} is not a running entry anymore"
            if integrator
            else "the integrator lock is held; the next integrator run takes it",
        }
    except Exception as e:  # one bad step must not sink the pass
        return {"item": a.item, "applied": False, "note": f"failed: {e!r}"}
