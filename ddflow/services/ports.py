"""Carrying a fix to another release line, inside the port's own worktree (RESEARCH R17).

A port is an ordinary item -- its own branch, gates, merge or request -- generated when a
fix is filed for several lines. What is special is only its first step, done here when
the port is claimed and its tree has just been made from the line's branch:

* **cherry-pick** applies what the fix LANDED (the target's range before..after its
  merge -- exact whatever the merge strategy was) with a three-way apply;
* **forward-merge** merges the previous line's branch into this one.

A conflict is not a failure of the workflow; it is the work. The tree is left with the
conflict markers in place and the claim says which files, so the agent resolves them,
commits, and carries on through the gates like any other task. Only a port that cannot
even START (the source's landing is unknown) is reported as failed -- and even then the
tree is left clean for a manual port rather than half-applied.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from ..core import flow as F
from ..core.model import DONE, State
from ..infra import git as GIT
from ..infra import worktree as W
from ..infra.log import EventLog
from . import flow as FS

CLEAN, CONFLICT, FAILED, EMPTY = "clean", "conflict", "failed", "empty"
NOT_READY, MANUAL = "not_ready", "manual"
#: Unknown is not "no conflicts": a half-done merge listed as clean would be committed over.
_UNMERGED_UNKNOWN = (
    "git could not list the unmerged paths, so the result is unknown: inspect the tree"
)


def apply(
    repo: Path, cfg: Config, log: EventLog, st: State, item_id: str, tree: Path
) -> dict[str, Any]:
    """Apply the port for ``item_id`` in ``tree``. Idempotent: an applied port is not redone."""
    it = st.items[item_id]
    if it.port:
        return dict(it.port)
    if it.promote_to:
        from . import promotions as PM

        out = PM.apply(repo, cfg, tree, it)
        log.append("port.applied", item_id, out)
        return out
    if not it.port_from:
        return {}
    src = st.items.get(it.port_from)
    if src is not None and src.state != DONE:
        # Reachable only through `claim --force`. Recording "clean" here -- which a
        # forward-merge of a branch that does not yet hold the fix happily reports --
        # marked the port done with nothing carried, and it was never applied again.
        # Not recorded, so a later claim applies it.
        return {
            "status": NOT_READY,
            "reason": f"{src.id} has not landed yet ({src.state}); the port is applied "
            f"when you claim {item_id} again after it has.",
        }
    line = F.effective_line(st, it) or cfg.flow.current_line
    out: dict[str, Any] = {
        "strategy": it.port_strategy,
        "from": it.port_from,
        "line": line,
        "kind": it.kind,
    }
    if src is None:
        out.update(status=FAILED, reason=f"source {it.port_from} is not in the queue")
    elif it.port_strategy == F.CHERRY_PICK:
        _cherry_pick(tree, it.id, src, out)
    else:
        _forward_merge(repo, cfg, st, tree, it.id, src, out)
    log.append("port.applied", item_id, out)
    return out


def _cherry_pick(tree: Path, item_id: str, src, out: dict[str, Any]) -> None:
    if not (src.landed_before and src.landed_after):
        out.update(
            status=FAILED,
            reason=f"{src.id}'s landing range is not recorded, so there is nothing exact to "
            f"apply. Port it by hand (`git cherry-pick -x {src.merged_sha or '<sha>'}`), "
            f"commit, and continue.",
        )
        return
    patch = W.git(tree, "diff", "--binary", src.landed_before, src.landed_after)
    if not patch.ok:
        out.update(status=FAILED, reason=f"could not read {src.id}'s change: {patch.err}")
        return
    if not patch.out.strip():
        out.update(status=EMPTY, reason=f"{src.id} landed no change to carry")
        return
    r = W.apply_3way(tree, patch.out + "\n")
    conflicts = GIT.unmerged(tree)
    if conflicts is None:
        # The tree is left as the apply made it, for the agent to inspect: a reset here
        # could discard a conflict the listing failed to show.
        out.update(status=FAILED, reason=_UNMERGED_UNKNOWN)
        return
    if conflicts:
        out.update(status=CONFLICT, files=conflicts)
        return
    if not r.ok:
        # Not a conflict: the patch did not apply at all (a file absent on this line).
        # Reset so the agent starts from a clean tree, not a partial application.
        W.git(tree, "reset", "--hard", "--quiet")
        out.update(status=FAILED, reason=f"the change does not apply to this line: {r.err[:300]}")
        return
    msg = (
        f"port {src.id} to this line: {src.title}\n\n"
        f"Item: {item_id}\nPorted-from: {src.id} {src.landed_after[:12]}"
    )
    c = W.git(tree, "commit", "-m", msg)
    out.update(status=CLEAN if c.ok else FAILED, **({} if c.ok else {"reason": c.err}))


def _forward_merge(
    repo: Path, cfg: Config, st: State, tree: Path, item_id: str, src, out: dict[str, Any]
) -> None:
    source = FS.target(repo, cfg, src, st)
    if cfg.flow.integration == "pr" and W.fetch(repo, cfg.flow.remote, source).ok:
        remote = f"{cfg.flow.remote}/{source}"
        if W.rev(repo, remote):
            source = remote
    out["source"] = source
    r = W.git(
        tree,
        "merge",
        "--no-ff",
        "-m",
        f"forward-merge {source} (carries {src.port_of or src.id})\n\nItem: {item_id}",
        source,
    )
    conflicts = GIT.unmerged(tree)
    landed = src.landed_after or src.merged_sha
    if conflicts is None:
        W.git(tree, "merge", "--abort")  # as the failed-merge branch below, and promotions
        out.update(status=FAILED, reason=_UNMERGED_UNKNOWN)
    elif conflicts:
        out.update(status=CONFLICT, files=conflicts)
    elif r.ok and landed and not W.git(tree, "merge-base", "--is-ancestor", landed, "HEAD").ok:
        # "Already up to date" is also r.ok. Checked, not assumed: a stale ref (a fetch
        # that failed in PR mode, where the local branch is never updated) merges cleanly
        # and carries nothing.
        out.update(
            status=FAILED,
            reason=f"merged {source}, but {src.id}'s landed change ({landed[:12]}) is not in "
            f"it -- the ref is stale. Fetch it and merge {source} by hand.",
        )
    elif r.ok:
        out.update(status=CLEAN)
    else:
        W.git(tree, "merge", "--abort")
        out.update(status=FAILED, reason=f"merging {source} failed: {r.err[:300]}")


def manual(repo: Path, cfg: Config, st: State, item_id: str) -> dict[str, Any]:
    """What to do when ddflow will not touch the tree: adopted, or no tree at all."""
    it = st.items[item_id]
    if it.promote_to:
        return {
            "status": MANUAL,
            "reason": f"{item_id} promotes {it.promote_from} to {it.promote_to}, and this tree "
            f"was not made by ddflow: `git merge --no-ff {it.promote_from}` on a branch from "
            f"{it.promote_to}, commit, then run the promotion gates.",
        }
    src = st.items.get(it.port_from)
    how = (
        f"`git merge --no-ff {FS.target(repo, cfg, src, st) if src else '<source line>'}`"
        if it.port_strategy == F.FORWARD_MERGE
        else f"`git diff {src.landed_before} {src.landed_after} | git apply --3way`"
        if src and src.landed_before
        else f"`git cherry-pick -x {src.merged_sha if src else '<sha>'}`"
    )
    return {
        "status": MANUAL,
        "reason": f"{item_id} is a {it.port_strategy} port of {it.port_of}, and this tree was "
        f"not made by ddflow, so it is left to you: {how}, commit, then run the gates.",
    }


def advice(port: dict[str, Any], item_id: str) -> str:
    """What the agent should do next, in one line, for the claim result."""
    status = port.get("status", "")
    if status == CONFLICT:
        return (
            f"port CONFLICTED in {', '.join(port.get('files', []))}: resolve the markers, "
            f"`git add` them and `git commit`, then continue {item_id}'s gates."
        )
    if status == FAILED:
        return f"port could not be applied: {port.get('reason', '')}"
    if status == EMPTY:
        return f"nothing to port ({port.get('reason', '')}); consider `ddflow abandon {item_id}`."
    if status == CLEAN:
        return "port applied and committed; run the gates as for any task."
    if status in (NOT_READY, MANUAL):
        return port.get("reason", "")
    return ""
