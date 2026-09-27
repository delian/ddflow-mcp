"""Pull requests and versions, as Outcomes for both surfaces (RESEARCH R16).

`merge` in PR mode lives in `api/lifecycle.py` beside local merge, because it is the SAME
verb: an agent's loop does not change when the repository starts requiring approvals.
What is here is what has no local-merge counterpart: asking the forge what reviewers did
(`pr sync`), reading the queue's view of it without asking (`pr status`), and versions.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.model import REVIEW
from ._base import _load


def _report_data(rep) -> dict[str, Any]:
    return {
        "checked": rep.checked,
        "changes": [asdict(c) for c in rep.changes],
        "waiting": rep.waiting,
        "unavailable": rep.unavailable,
        "refused": rep.refused,
    }


def pr_sync(repo: Path, *, item: str = "", agent: str = "") -> O.Outcome:
    """Ask the forge about every request in REVIEW and record what reviewers did.

    Exit 2 when the forge could not be asked about ANY of them -- "could not look" must
    never read as "nothing changed", which is the whole reason `unavailable` exists.
    Partial reachability is exit 0 with the gaps listed.
    """
    from ..services import flow as FS

    log, cfg, _st = _load(repo, agent)
    rep = FS.sync(repo, cfg, log, only=item)
    data = _report_data(rep)
    if rep.refused and not rep.checked:
        return O.refused("pr.sync", "; ".join(rep.refused), **data)
    if rep.unavailable and not rep.changes and not rep.waiting:
        return O.nothing("pr.sync", "; ".join(rep.unavailable), **data)
    if not rep.checked:
        return O.nothing("pr.sync", "nothing is in review", **data)
    return O.ok("pr.sync", **data)


def pr_status(repo: Path, *, agent: str = "") -> O.Outcome:
    """Every item with a request, from the LOG -- no forge call. `synced_at` says how old."""
    _log, _cfg, st = _load(repo, agent)
    rows = []
    for it in sorted(st.items.values(), key=lambda i: i.id):
        if it.pr is None or it.removed:
            continue
        pr = it.pr
        rows.append(
            {
                "id": it.id,
                "state": it.state,
                "number": pr.number,
                "url": pr.url,
                "base": pr.base,
                "head": pr.head,
                "pr_state": pr.state,
                "review": pr.review,
                "checks": pr.checks,
                "rounds": pr.rounds,
                "synced_at": pr.synced_at,
                "feedback": pr.feedback,
            }
        )
    releases = [{"version": v, **p} for v, p in sorted(st.pending_releases.items())]
    in_review = sum(1 for r in rows if r["state"] == REVIEW)
    return O.ok("pr.status", rows=rows, in_review=in_review, releases=releases)


def _plan_data(vp) -> dict[str, Any]:
    return {
        "ref": vp.ref,
        "current": vp.current,
        "current_tag": vp.current_tag,
        "next": vp.next,
        "bump": vp.bump,
        "reasons": vp.reasons,
        "commits": vp.commits,
        "items": vp.items,
        "notes": vp.notes,
        "problems": vp.problems,
    }


def version_show(repo: Path, *, bump: str = "", agent: str = "") -> O.Outcome:
    """The current version, the next one, why, and the release notes. Reads only."""
    from ..services import flow as FS

    _log, cfg, st = _load(repo, agent)
    vp = FS.plan_version(repo, cfg, st, bump=bump)
    data = _plan_data(vp)
    if vp.problems:
        return O.refused("version.show", "; ".join(vp.problems), **data)
    if not vp.next:
        return O.nothing(
            "version.show",
            f"nothing to release on {vp.ref} since {vp.current_tag or 'the start'}",
            **data,
        )
    return O.ok("version.show", **data)


def version_cut(
    repo: Path,
    *,
    bump: str = "",
    version: str = "",
    push: bool = False,
    dry_run: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Tag the next version (gitflow: through a release branch). Exit 2 = nothing to release."""
    from ..services import flow as FS

    log, cfg, _st = _load(repo, agent)
    c = FS.cut(repo, cfg, log, bump=bump, version=version, push=push, dry_run=dry_run)
    data: dict[str, Any] = {
        "version": c.version,
        "tag": c.tag,
        "sha": c.sha,
        "pushed": c.pushed,
        "url": c.url,
        "steps": c.steps,
        "dry_run": dry_run,
        "notes": c.plan.notes if c.plan else "",
        "warning": c.reason if c.ok else "",
    }
    if c.refused:
        return O.refused("version.cut", c.reason, **data)
    if c.unavailable or c.nothing:
        return O.nothing("version.cut", c.reason, **data)
    return O.ok("version.cut", **data)
