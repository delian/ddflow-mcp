"""Environment branches: promoting work downstream, one step at a time (RESEARCH R18).

GitLab flow's environment branches -- `main -> pre-production -> production` -- each
mirror what is deployed there, and work moves only DOWNSTREAM, only by merging the branch
immediately upstream ("upstream first"). A promotion here is an ordinary task: its tree is
made from the environment branch, the upstream branch is merged into it (a conflict is left
for the agent, like a port's), it runs `gates.promotion_pipeline`, and it lands by local
merge or by a merge request whose approval IS the deploy approval.

Nothing else can land on an environment branch: no item targets one except a promotion,
so a fix made "straight on production" is not a thing ddflow can be asked to do.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import flow as F
from ..core.model import ABANDONED, DONE, Item, State
from ..infra import worktree as W
from ..infra.log import EventLog
from . import flow as FS


class PromotionError(ValueError):
    """Refused: unknown environment, one already open, a branch that does not exist."""


def _open(st: State, env: str) -> list[Item]:
    return [
        i
        for i in st.items.values()
        if i.promote_to == env and not i.removed and i.state not in (DONE, ABANDONED)
    ]


def _ahead(repo: Path, cfg: Config, frm: str, to: str) -> int:
    """Commits on ``frm`` not yet on ``to`` (the forge's copies in PR mode); -1 if unknown."""
    a, b = FS._ref(repo, cfg, frm), FS._ref(repo, cfg, to)
    if not W.rev(repo, a) or not W.rev(repo, b):
        return -1
    # A commit of the event log alone (`[log].commit_events`, Bcd3512c891) is not work
    # to promote: only commits that touch something outside .ddflow/events count.
    r = W.git(
        repo, "rev-list", "--count", "--no-merges", f"{b}..{a}", "--", ".",
        ":(exclude).ddflow/events",
    )  # fmt: skip
    return int(r.out) if r.ok and r.out.isdigit() else -1


def add(
    repo: Path, cfg: Config, log: EventLog, st: State, env: str, *, force: bool = False
) -> dict[str, Any]:
    """File a promotion to ``env``. Returns {"id", "from", "to", "ahead"}; {"id": ""} when
    there is nothing to promote. Raises PromotionError when it may not be filed."""
    try:
        frm, to = F.promotion_step(cfg, W.default_branch(repo), env)
    except ValueError as exc:
        raise PromotionError(str(exc)) from exc
    busy = _open(st, to)
    if busy:
        raise PromotionError(
            f"a promotion to {to} is already open ({busy[0].id}, {busy[0].state}). One at a "
            f"time: two would each carry a different snapshot of {frm}."
        )
    ahead = _ahead(repo, cfg, frm, to)
    if ahead < 0:
        missing = [b for b in (frm, to) if not W.rev(repo, FS._ref(repo, cfg, b))]
        raise PromotionError(
            f"branch {', '.join(missing) or frm + '/' + to} does not exist. Create the "
            f"environment branch first (e.g. `git branch {to} {frm}`)."
        )
    if ahead == 0 and not force:
        return {"id": "", "from": frm, "to": to, "ahead": 0}
    n = 1 + sum(1 for i in st.items.values() if i.promote_to == to)
    pid = f"promote-{F.safe_name(to)}-{n}"
    log.append(
        "task.added",
        pid,
        {
            "title": f"Promote {frm} to {to}",
            "body": f"Merge {frm} into {to} ({ahead} commit(s)), run the promotion "
            f"pipeline, land it on {to}.",
            "tags": ["promotion"],
            "globs": [],
            "promote_from": frm,
            "promote_to": to,
        },
    )
    return {"id": pid, "from": frm, "to": to, "ahead": ahead}


def auto(repo: Path, cfg: Config, log: EventLog, st: State) -> list[str]:
    """File the promotions `auto_promote` asks for. Quietly skips an environment that
    is busy or up to date -- that is the normal state, not an error."""
    made = []
    for env in cfg.flow.auto_promote:
        try:
            out = add(repo, cfg, log, st, env)
        except PromotionError:
            continue
        if out["id"]:
            made.append(out["id"])
    return made


def status(repo: Path, cfg: Config, st: State) -> list[dict[str, Any]]:
    chain = F.env_chain(cfg, W.default_branch(repo))
    rows = []
    for up, env in pairwise(chain):
        ref = FS._ref(repo, cfg, env)
        done = [
            i
            for i in st.items.values()
            if i.promote_to == env and i.state == DONE and not i.removed
        ]
        last = max(done, key=lambda i: i.completed_at) if done else None
        dep = st.deployments.get(env)
        live = dep["sha"] if dep else ""
        head = W.rev(repo, ref)
        undeployed = -1
        if live and head:
            r = W.git(repo, "rev-list", "--count", f"{live}..{head}")
            undeployed = int(r.out) if r.ok and r.out.isdigit() else -1
        rows.append(
            {
                "env": env,
                "deployed": live[:12],
                "deployed_at": dep["at"] if dep else "",
                "undeployed": undeployed,
                "from": up,
                "exists": bool(W.rev(repo, ref)),
                "head": W.rev(repo, ref)[:12],
                "behind": _ahead(repo, cfg, up, env),
                "open": [i.id for i in _open(st, env)],
                "last": last.id if last else "",
                "last_at": last.completed_at if last else "",
                "auto": env in cfg.flow.auto_promote,
            }
        )
    return rows


def deployed(repo: Path, cfg: Config, log: EventLog, env: str, *, sha: str = "") -> dict[str, Any]:
    """Record that ``sha`` is what is now RUNNING in ``env`` (a deploy hook calls this).

    Defaults to the environment branch's head. Raises PromotionError for an environment
    that is not in the chain or a ``sha`` that is not a commit here.
    """
    chain = F.env_chain(cfg, W.default_branch(repo))
    if env not in chain[1:]:
        raise PromotionError(
            f"{env!r} is not an environment ({', '.join(chain[1:]) or 'none configured'})"
        )
    full = W.rev(repo, sha) if sha else W.rev(repo, FS._ref(repo, cfg, env))
    if not full:
        what = f"{sha!r} is not a commit in this repository" if sha else f"branch {env} is missing"
        raise PromotionError(what)
    log.append("deploy.recorded", env, {"env": env, "sha": full})
    return {"env": env, "sha": full}


def apply(repo: Path, cfg: Config, tree: Path, it: Item) -> dict[str, Any]:
    """Merge the upstream branch into the promotion's tree. Mirrors a forward-merge port."""
    source = FS._ref(repo, cfg, it.promote_from)
    out: dict[str, Any] = {
        "strategy": "promote",
        "from": it.promote_from,
        "line": it.promote_to,
        "kind": it.kind,
        "source": source,
    }
    head = W.rev(repo, source)
    r = W.git(
        tree,
        "merge",
        "--no-ff",
        "-m",
        f"promote {it.promote_from} to {it.promote_to}\n\nItem: {it.id}",
        source,
    )
    conflicts = [
        ln
        for ln in W.git(tree, "diff", "--name-only", "--diff-filter=U").out.splitlines()
        if ln.strip()
    ]
    if conflicts:
        out.update(status="conflict", files=conflicts)
    elif not r.ok:
        W.git(tree, "merge", "--abort")
        out.update(status="failed", reason=f"merging {source} failed: {r.err[:300]}")
    elif head and not W.git(tree, "merge-base", "--is-ancestor", head, "HEAD").ok:
        out.update(status="failed", reason=f"{source} ({head[:12]}) is not in the result")
    elif "Already up to date" in (r.out + r.err):
        out.update(
            status="empty", reason=f"{it.promote_to} already has everything on {it.promote_from}"
        )
    else:
        out.update(status="clean", sha=head)
    return out
