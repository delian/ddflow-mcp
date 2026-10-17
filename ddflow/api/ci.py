"""`ddflow ci run|status`: the CI parity gate's command (D-ci-parity)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..core import outcome as O
from ..services import ci as CI
from ..services import leasekeep as LK
from ._base import _load
from ._dedupe import Answer
from .knowledge import bug_found


def _record(log, stage: str, res: CI.Result, subject: str = "ci") -> None:
    log.append(
        "ci.result",
        subject,
        {
            "stage": stage,
            "status": res.status,
            "ok": res.ok,
            "sha": res.sha,
            "checks": [{"id": c.id, "ok": c.ok, "detail": c.detail[:300]} for c in res.checks],
        },
    )


def lease_tick(log, cfg, st, item: str) -> tuple[Callable[[], None], float] | None:
    """``tick`` for `CI.run`: renews the caller's own lease on ``item`` every
    `[lease].heartbeat_s` while the command runs (DX-long-op-renew). None when it holds none."""
    every = float(max(1, cfg.lease.heartbeat_s))
    tick = LK.ticker(log, cfg, st.items.get(item), every) if item else None
    return (tick, every) if tick else None


def file_failures(
    repo: Path, res: CI.Result, *, stage: str, agent: str = ""
) -> tuple[list[str], dict[str, str]]:
    """A bug and its fix task per failing check, once while the bug is open (the dedupe key
    is the check id: Bci-<check>). Returns the bug ids this call filed, and the fix task of each that got one."""
    _log, cfg, st = _load(repo, agent)
    filed: list[str] = []
    fixes: dict[str, str] = {}
    failing = [c for c in res.checks if not c.ok]
    for c in failing:
        bid = CI.bug_id(c.id, cfg)
        # Open under the base id or under any sha-suffixed re-file of it: one bug while open.
        if any(b.open and CI.is_filing_of(k, bid, cfg) for k, b in st.bugs.items()):
            continue
        if bid in st.bugs:  # fixed before and failing again: a new bug, not a reopening
            bid = CI.refile_id(bid, res.sha)
        out = bug_found(
            repo,
            id=bid,
            title=f"{stage} CI check failed: {c.id}",
            summary=(
                f"The CI check {c.id!r} fails on {res.sha[:12] or 'the base'} ({stage}). "
                f"{c.detail}\nRun `ddflow ci run` to see it."
            ),
            severity="high",
            answer=Answer(relation="new"),
            agent=agent,
        )
        if out.ok:
            filed.append(bid)
            if out.data.get("fix_task"):
                fixes[bid] = out.data["fix_task"]
    return filed, fixes


def check_after_merge(
    repo: Path, *, sha: str = "", item: str = "", agent: str = "", since: str = ""
) -> dict:
    """`[ci].on_merge`: run the CI command on the base a merge just landed on, record the
    result and file the failures. Never raises into the merge: it already landed.
    Returns what to say about it ({} when nothing ran). ``since`` is the base the merge started
    from: the tripwire hooks run on that range, not over every file."""
    log, cfg, st = _load(repo, agent)
    mode = cfg.ci.on_merge
    if mode == "off":
        return {}
    if mode not in CI.ON_MERGE_MODES:
        modes = " | ".join(CI.ON_MERGE_MODES)
        return {"status": "unavailable", "why": f"[ci].on_merge={mode!r}: {modes}"}
    cmd, _why = CI.main_command(repo, cfg)
    if not cmd:
        return {}  # no CI command in this project: nothing to check, nothing to claim
    res = CI.run(
        repo, cfg, ref=sha or "HEAD", command=cmd, changed_from=since,
        tick=lease_tick(log, cfg, st, item),
    )  # fmt: skip
    _record(log, "merge", res, item or "ci")
    filed, fixes = (
        ([], {}) if res.status != "failed" else file_failures(repo, res, stage="merge", agent=agent)
    )
    return {
        "status": res.status,
        "failed": [c.id for c in res.checks if not c.ok],
        "bugs": filed,
        "fix_tasks": fixes,
        **({"why": res.reason} if res.status != "passed" else {}),
    }


def record(
    repo: Path, *, stage: str, ok: bool, sha: str = "", report: str = "", agent: str = ""
) -> O.Outcome:
    """`ddflow ci record`: write down a CI outcome that ran elsewhere (the pre-push hook).
    `report` is the file holding the pre-commit output, parsed for the failing checks."""
    if stage not in ("gate", "merge", "pre-push", "schedule"):
        return O.refused(
            "ci.record", f"unknown stage {stage!r}: gate | merge | pre-push | schedule"
        )
    log, _cfg, _st = _load(repo, agent)
    checks: list[CI.Check] = []
    if report:
        try:
            checks = CI.parse_checks(Path(report).read_text("utf-8", errors="replace"))
        except OSError as e:
            return O.failed("ci.record", f"cannot read the report {report!r}: {e}")
    res = CI.Result("passed" if ok else "failed", checks=checks, sha=sha)
    _record(log, stage, res)
    return O.ok(
        "ci.record", stage=stage, status=res.status, failed=[c.id for c in checks if not c.ok]
    )


def status(repo: Path, *, agent: str = "") -> O.Outcome:
    """What `ci run` would execute here, and whether it can."""
    _log, cfg, _st = _load(repo, agent)
    cmd, why = CI.resolve_command(repo, cfg)
    missing = CI.tool_missing(cmd) if cmd else ""
    data = {
        "command": cmd,
        "base": cfg.ci.base or "(default branch)",
        "timeout_s": cfg.ci.timeout_s,
        "available": bool(cmd) and not missing,
        "why": why or (f"{missing!r} is not installed" if missing else ""),
    }
    return O.ok("ci.status", **data)
