"""`ddflow ci run|status`: the CI parity gate's command (D-ci-parity)."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import ci as CI
from . import knowledge as K
from ._base import _load


def run(
    repo: Path, *, ref: str = "HEAD", base: str = "", command: str = "", agent: str = ""
) -> O.Outcome:
    """Run the CI command on `ref` merged with `base`. 0 passed, 1 failed, 2 could not run."""
    _log, cfg, st = _load(repo, agent)
    item = st.items.get(ref)
    if item is not None and item.branch:  # an item id names its branch
        ref = item.branch
    res = CI.run(repo, cfg, ref=ref, base=base, command=command)
    data = res.as_data()
    if res.ok:
        return O.ok("ci.run", **data)
    if res.status == "failed":
        return O.Outcome(kind="ci.run", data=data, exit=O.FAIL, reason=res.reason)
    return O.nothing("ci.run", res.reason, **data)


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


def file_failures(repo: Path, res: CI.Result, *, stage: str, agent: str = "") -> list[str]:
    """A bug and its fix task per failing check, once while the bug is open (the dedupe key
    is the check id: Bci-<check>). Returns the bug ids this call filed."""
    _log, _cfg, st = _load(repo, agent)
    filed: list[str] = []
    failing = [c for c in res.checks if not c.ok]
    for c in failing:
        bid = CI.bug_id(c.id)
        held = st.bugs.get(bid)
        if held is not None and held.open:
            continue
        if held is not None:  # fixed before and failing again: a new bug, not a reopening
            bid = f"{bid}-{res.sha[:7]}"
            if (st.bugs.get(bid) is not None) and st.bugs[bid].open:
                continue
        out = K.bug_found(
            repo,
            id=bid,
            title=f"{stage} CI check failed: {c.id}",
            summary=(
                f"The CI check {c.id!r} fails on {res.sha[:12] or 'the base'} ({stage}). "
                f"{c.detail}\nRun `ddflow ci run` to see it."
            ),
            severity="high",
            answer=K.DD.Answer(relation="new"),
            agent=agent,
        )
        if out.ok:
            filed.append(bid)
    return filed


def check_after_merge(repo: Path, *, sha: str = "", item: str = "", agent: str = "") -> dict:
    """`[ci].on_merge`: run the CI command on the base a merge just landed on, record the
    result and file the failures. Never raises into the merge: it already landed.
    Returns what to say about it ({} when nothing ran)."""
    log, cfg, _st = _load(repo, agent)
    mode = cfg.ci.on_merge
    if mode == "off":
        return {}
    if mode not in CI.ON_MERGE_MODES:
        return {"status": "unavailable", "why": f"[ci].on_merge={mode!r}: off | fast | full"}
    cmd, _why = CI.main_command(repo, cfg)
    if not cmd:
        return {}  # no CI command in this project: nothing to check, nothing to claim
    res = CI.run(repo, cfg, ref=sha or "HEAD", command=cmd)
    _record(log, "merge", res, item or "ci")
    filed = [] if res.status != "failed" else file_failures(repo, res, stage="merge", agent=agent)
    return {
        "status": res.status,
        "failed": [c.id for c in res.checks if not c.ok],
        "bugs": filed,
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


def ci_tool(
    repo: Path,
    *,
    action: str = "status",
    ref: str = "HEAD",
    base: str = "",
    command: str = "",
    agent: str = "",
) -> O.Outcome:
    """The `ddflow_ci` tool: action run | status."""
    if action == "run":
        return run(repo, ref=ref or "HEAD", base=base, command=command, agent=agent)
    if action == "status":
        if base or command or (ref and ref != "HEAD"):
            return O.refused("ci", "ref, base and command are for action=run")
        return status(repo, agent=agent)
    return O.refused("ci", f"unknown action {action!r}: run | status")
