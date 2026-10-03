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
    if not rep.checked and not rep.changes and not rep.waiting:
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
    back_merges = [dict(r) for _k, r in sorted(st.back_merges.items())]
    return O.ok(
        "pr.status", rows=rows, in_review=in_review, releases=releases, back_merges=back_merges
    )


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
        "line": vp.line,
    }


def version_show(repo: Path, *, bump: str = "", line: str = "", agent: str = "") -> O.Outcome:
    """The current version, the next one, why, and the release notes. Reads only."""
    from ..services import flow as FS

    _log, cfg, st = _load(repo, agent)
    vp = FS.plan_version(repo, cfg, st, bump=bump, line=line)
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
    line: str = "",
    changelog: bool = False,
    force: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Tag the next version (gitflow: through a release branch). Exit 2 = nothing to release.

    ``changelog`` also writes the version's section into CHANGELOG.md as part of the cut
    (refused, exit 3, over a hand-edited file unless ``force``; git failure is exit 2)."""
    from ..services import flow as FS

    log, cfg, _st = _load(repo, agent)
    c = FS.cut(
        repo,
        cfg,
        log,
        bump=bump,
        version=version,
        push=push,
        dry_run=dry_run,
        line=line,
        changelog=changelog,
        force=force,
    )
    data: dict[str, Any] = {
        "version": c.version,
        "tag": c.tag,
        "sha": c.sha,
        "pushed": c.pushed,
        "url": c.url,
        "steps": c.steps,
        "dry_run": dry_run,
        "changelog": c.changelog,
        "notes": c.plan.notes if c.plan else "",
        "warning": c.reason if c.ok else "",
    }
    if c.refused:
        return O.refused("version.cut", c.reason, **data)
    if c.unavailable or c.nothing:
        return O.nothing("version.cut", c.reason, **data)
    return O.ok("version.cut", **data)


def flow_show(repo: Path, *, agent: str = "") -> O.Outcome:
    """How this project works: the model, its release lines, and every workflow choice --
    its value, who made it (config, a recorded choice, or a default nobody chose) and when."""
    from ..core import flow as F
    from ..services import choices as CH

    _log, cfg, st = _load(repo, agent)
    rows = CH.report(cfg, st)
    lines = [
        {"line": ln, "branch": cfg.flow.lines.get(ln, ""), "current": ln == cfg.flow.current_line}
        for ln in F.line_order(cfg)
    ]
    return O.ok(
        "flow.show",
        choices=rows,
        pending=[r["knob"] for r in rows if r["relevant"] and not r["decided"]],
        lines=lines,
        problems=F.problems(cfg),
    )


def flow_choose(
    repo: Path, knob: str, value: str, *, reason: str = "", agent: str = ""
) -> O.Outcome:
    """Record a workflow choice, attributed. The config file still wins over it, and the
    result says so when it does -- a choice that is not in effect must not look like one."""
    from ..services import choices as CH

    log, cfg, _st = _load(repo, agent)
    bad = CH.validate(knob, value)
    if bad:
        return O.failed("flow.chosen", bad, knob=knob, value=value, in_effect=False)
    CH.choose(log, knob, value, reason=reason)
    source = cfg.sources.get(f"flow.{knob}", "default")
    overridden = source in ("file", "env")
    from ..core.model import REVIEW, RUNNING

    in_flight = [i.id for i in _st.items.values() if i.state in (RUNNING, REVIEW)]
    shift = (
        f" {len(in_flight)} item(s) in flight ({', '.join(in_flight[:5])}) were started under "
        f"the previous value and keep their branches; the new value applies to new work."
        if in_flight and knob in ("model", "integration") and not overridden
        else ""
    )
    return O.ok(
        "flow.chosen",
        knob=knob,
        value=str(value).lower(),
        in_effect=not overridden,
        note=(
            f"recorded, but NOT in effect: {knob} is set in the {source} config, which wins. "
            f"Change it there."
            if overridden
            else shift.strip()
        ),
    )


def promote_add(repo: Path, env: str, *, force: bool = False, agent: str = "") -> O.Outcome:
    """File a promotion to ``env`` from the branch immediately upstream of it.
    Exit 2 = nothing to promote; exit 3 = refused (unknown, busy, missing branch)."""
    from ..services import promotions as PM

    log, cfg, st = _load(repo, agent)
    try:
        out = PM.add(repo, cfg, log, st, env, force=force)
    except PM.PromotionError as exc:
        return O.refused("promote.added", str(exc), id="", env=env, ahead=0)
    data = {"id": out["id"], "env": out["to"], "from": out["from"], "ahead": out["ahead"]}
    if not out["id"]:
        return O.nothing(
            "promote.added", f"{out['to']} already has everything on {out['from']}", **data
        )
    return O.ok("promote.added", **data)


def promote_deployed(repo: Path, env: str, *, sha: str = "", agent: str = "") -> O.Outcome:
    """Record the sha a deploy put live in ``env`` (default: the environment branch's head),
    so `promote status` answers "what is live" exactly. Exit 3 = refused."""
    from ..services import promotions as PM

    log, cfg, _st = _load(repo, agent)
    try:
        out = PM.deployed(repo, cfg, log, env, sha=sha)
    except PM.PromotionError as exc:
        return O.refused("deploy.recorded", str(exc), env=env, sha="")
    return O.ok("deploy.recorded", **out)


def promote_status(repo: Path, *, agent: str = "") -> O.Outcome:
    """Each environment: where it stands, how far behind its upstream, what is open."""
    from ..services import promotions as PM

    _log, cfg, st = _load(repo, agent)
    rows = PM.status(repo, cfg, st)
    if not rows:
        return O.nothing("promote.status", "no environments: set [flow].environments", rows=[])
    return O.ok("promote.status", rows=rows)
