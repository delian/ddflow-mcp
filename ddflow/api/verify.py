"""`ddflow verify <id>`: the claims behind a completion, re-derived (B-verify-check)."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import verify as V
from ._base import _load

_BUG_PREFIX = "verify: the completion of "


def verify(
    repo: Path,
    item: str,
    *,
    reopen: bool = False,
    reason: str = "",
    force: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Re-derive a completion's claims. For an item that is NOT done, say whether its work
    nevertheless appears to have landed (the false-negative direction). With `reopen`, a
    completion that does not hold goes back to the queue (`force` for one that does)."""
    log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        return O.failed("verify", f"no such item {item!r}", id=item)
    rep = V.check(repo, cfg, st, log.read_all(), item)
    data = rep.as_data()
    if not rep.completed:
        from ..services import backfill as BF

        found = BF.find_commit(repo, st, item) if it.state != "done" else None
        if found:
            data["appears_landed"] = {"sha": found[0], "how": found[1]}
            return O.nothing(
                "verify",
                f"{item} is {it.state}, but its work appears to have landed ({found[0][:10]}, "
                f"found by {found[1]}). If it did, `ddflow complete {item} --sha {found[0][:10]} "
                f"--force` records it done without redoing it; the override is recorded.",
                **data,
            )
        return O.nothing(
            "verify", f"{item} is {it.state}, not done: there is no completion to verify", **data
        )
    if reopen:
        return _reopen(log, rep, data, reason=reason, force=force)
    if rep.failed:
        bad = "; ".join(f"{c.id}: {c.detail}" for c in rep.claims if c.status == V.FAIL)
        return O.Outcome(kind="verify", data=data, exit=O.FAIL, reason=f"{item}: {bad}")
    return O.ok("verify", **data)


def verify_sweep(
    repo: Path,
    *,
    phase: str = "",
    limit: int = 20,
    file_bugs: bool = False,
    agent: str = "",
) -> O.Outcome:
    """`ddflow verify --phase/--all`: every done task checked, the worst listed first.

    Exit 1 when any completion's claim does not hold. With `file_bugs`, each such item
    gets a bug (the add-time duplicate check makes a re-run file nothing twice)."""
    log, cfg, st = _load(repo, agent)
    if phase and phase not in st.items:
        return O.failed("verify.sweep", f"no such phase {phase!r}", phase=phase)
    scope = st.descendants(phase) if phase else None
    items = [
        i.id
        for i in st.items.values()
        if i.kind == "task"
        and i.state == "done"
        and not i.removed
        and (scope is None or i.id in scope)
    ]
    sw = V.sweep(repo, cfg, st, log.read_all(), items)
    data = sw.as_data(max(1, limit))
    failing = [r for _, r in sw.worst if r.failed]
    filed: list[str] = []
    if file_bugs:
        from .knowledge import bug_found

        already = {
            b.summary.split(" does not hold", 1)[0]
            for b in st.bugs.values()
            if not b.fixed_at and not b.invalid_at and b.summary.startswith(_BUG_PREFIX)
        }
        for rep in failing:
            if f"{_BUG_PREFIX}{rep.item}" in already:
                continue  # an open bug already says so
            bad = "; ".join(f"{c.id}: {c.detail}" for c in rep.claims if c.status == V.FAIL)
            out = bug_found(
                repo,
                summary=f"{_BUG_PREFIX}{rep.item} does not hold -- {bad}",
                item=rep.item,
                agent=agent,
            )
            if out.exit == O.OK:
                filed.append(rep.item)
    data["bugs_filed"] = filed
    if not items:
        return O.nothing("verify.sweep", "no completed tasks to verify", **data)
    if failing:
        return O.Outcome(
            kind="verify.sweep",
            data=data,
            exit=O.FAIL,
            reason=f"{len(failing)} of {sw.checked} completions do not hold",
        )
    return O.ok("verify.sweep", **data)


def refuse_sweep_args() -> O.Outcome:
    """`phase`, `limit` and `file_bugs` are sweep arguments; they cannot ride on one id."""
    return O.refused("verify", "phase, limit and file_bugs are for a sweep: omit id")


def _reopen(log, rep, data: dict, *, reason: str, force: bool) -> O.Outcome:
    """Append `item.reopened` for a completion that failed verification."""
    bad = [c for c in rep.claims if c.status == V.FAIL]
    if not bad and not force:
        return O.refused(
            "verify",
            f"{rep.item}: its completion holds ({rep.verdict}); nothing to reopen. "
            f'--force --reason "..." reopens it anyway, and says so in the log.',
            **data,
        )
    if not bad and not reason:
        return O.refused("verify", f"{rep.item}: --force needs a --reason", **data)
    why = reason or "; ".join(f"{c.id}: {c.detail}" for c in bad)
    log.append(
        "item.reopened",
        rep.item,
        {
            "reason": why,
            "claims": [
                {"id": c.id, "status": c.status, "detail": c.detail}
                for c in rep.claims
                if c.status != V.OK
            ],
            "forced": not bad,
        },
    )
    return O.ok("verify", reopened=True, reason_given=why, **data)
