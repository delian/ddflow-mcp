"""`chore commit` and `chore approve` (decision D-chore-class).

One command runs the ceremony a chore used to take ten: a task tagged `chore`, a claim,
the files copied into its worktree and committed, the gates skipped with the reason
recorded, the merge, and the completion -- which needs no reviewer because the change is
all inside `[chore].globs` and a person approved that list. Anything else is refused
before a task exists, and takes the normal pipeline.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from ..core import outcome as O
from ..core.digest import content_digest
from ..infra import worktree as W
from ..services import approval as AP
from ..services import chore as CO
from ..services import gates as G
from ._base import _load
from ._dedupe import Answer
from .gates import record as gate_record
from .items import task_add
from .lifecycle import claim, complete
from .lifecycle import merge as merge_item

KIND = "chore.committed"


def chore_approve(repo: Path, *, agent: str = "", note: str = "") -> O.Outcome:
    """A PERSON approves the CURRENT `[chore].globs`. Refused under an agent identity."""
    log, cfg, _st = _load(repo, agent)
    digest = CO.approval_digest(cfg)
    try:
        got = AP.grant(
            log, CO.SUBJECT, digest, note=note or ", ".join(cfg.chore.globs), requested_agent=agent
        )
    except AP.ApprovalRefused as exc:
        return O.refused("chore.approve", str(exc))
    return O.ok(
        "chore.approve",
        digest=digest,
        globs=list(cfg.chore.globs),
        approved_by=got.actor.user,
        text=f"chore list approved by {got.actor.user}: {', '.join(cfg.chore.globs)}",
    )


def _changes(repo: Path, rels: list[str]) -> list[str]:
    """The files under ``rels`` whose working copy differs from HEAD (new, modified, deleted)."""
    tracked = W.git(repo, "-c", "core.quotePath=false", "diff", "--name-only", "HEAD", "--", *rels)
    new = W.git(
        repo,
        "-c",
        "core.quotePath=false",
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        *rels,
    )
    names = (tracked.out or "").splitlines() + (new.out or "").splitlines()
    return [n.strip() for n in names if n.strip()]


def _failure(stage: str, item: str, out: O.Outcome, **data) -> O.Outcome:
    return O.Outcome(
        kind=KIND,
        data={"item": item, "stage": stage, **data},
        exit=out.exit,
        reason=f"chore {item}: {stage} failed: {out.reason} (the task stays open: "
        f"`ddflow show {item}`; `ddflow abandon {item}` drops it)",
    )


def _select(repo: Path, cfg, st, files: list[str], message: str):
    """The chore files to land and the approval behind them -- or the refusal."""
    if not message.strip():
        return O.refused(KIND, "a chore needs a message: -m MSG")
    rels: list[str] = []
    for name in files:
        rel = CO.relative(repo, name)
        if rel is None:
            return O.refused(KIND, f"{name} is outside the repository")
        rels.append(rel)
    if not rels:
        return O.refused(KIND, "no files named")
    todo = list(dict.fromkeys(_changes(repo, rels)))  # a directory stands for what changed in it
    if bad := CO.outside(cfg, [*rels, *todo]):
        return O.refused(
            KIND,
            f"not a chore: {', '.join(bad)} match no [chore].globs ({', '.join(cfg.chore.globs)})."
            f" Code takes the normal pipeline (`ddflow task add`, claim, review).",
            outside=bad,
        )
    approved = CO.standing(st, cfg)
    if not approved.ok:
        return O.refused(
            KIND,
            f"{approved.reason}. The chore class is a person's to approve: they run "
            f"`ddflow chore approve` from their own terminal.",
        )
    if not todo:
        return O.nothing(KIND, f"nothing to commit: {', '.join(rels)} match HEAD")
    return todo, approved


def _stage(repo: Path, wt: W.Worktree, todo: list[str], message: str) -> O.Outcome | None:
    """Copy ``todo`` into the item's tree, commit it there, then let the primary's copies go
    (left, they make the merge refuse over an untracked file or fight a modified one)."""
    for rel in todo:
        src, dst = repo / rel, wt.path / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            dst.unlink(missing_ok=True)
    made = W.commit(wt, message.strip(), todo)
    if not made.ok:
        return O.failed(KIND, (made.err or made.out).strip())
    return None


def _clear_primary(repo: Path, todo: list[str]) -> None:
    """Let the primary's copies of ``todo`` go: the branch holds them now, and left behind
    they make the merge refuse over an untracked file or fight a modified one. A path in
    HEAD is restored to it; one that is not (new, perhaps staged) is dropped from the index
    and the tree."""
    for rel in todo:
        if W.git(repo, "cat-file", "-e", f"HEAD:{rel}").ok:
            W.git(repo, "checkout", "HEAD", "--", rel)
        else:
            W.git(repo, "rm", "-q", "-f", "--cached", "--ignore-unmatch", "--", rel)
            (repo / rel).unlink(missing_ok=True)


def _bring_back(repo: Path, wt: W.Worktree, todo: list[str]) -> None:
    """After a failed merge, put the files back in the primary, so a failure costs no work."""
    for rel in todo:
        src = wt.path / rel
        if src.exists():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, repo / rel)


def _skip_gates(repo: Path, cfg, st, item: str, todo: list[str], note: str, agent: str):
    """Skip every pipeline gate the chore may skip, each with ``note``; the list skipped, or
    (the refusal, the gate)."""
    pipeline = G.status(st, cfg, item, G.load_gates(repo, cfg), diff=lambda: todo).pipeline
    skipped = []
    for gate in pipeline:
        if gate == "merge" or gate not in cfg.chore.skip_gates:
            continue
        out = gate_record(repo, item, gate, skip=True, reason=note, agent=agent)
        if not out.ok:
            return out, gate
        skipped.append(gate)
    return skipped, ""


def chore_commit(
    repo: Path,
    files: list[str],
    *,
    message: str,
    model: str = "",
    agent: str = "",
    called_from: Path | None = None,
) -> O.Outcome:
    """Commit ``files`` (changes in this checkout) as a chore and land it, in one step."""
    _log, cfg, st = _load(repo, agent)
    picked = _select(repo, cfg, st, files, message)
    if isinstance(picked, O.Outcome):
        return picked
    todo, approved = picked
    title = message.strip().splitlines()[0]
    item = f"chore-{CO.slug(message)}-{content_digest(f'{message}{time.time()}')[:4]}"
    out = task_add(
        repo,
        item,
        title=title,
        globs=",".join(todo),
        body=f"{message}\n\nChore (D-chore-class): {', '.join(todo)}",
        tags="chore",
        answer=Answer("new"),
        agent=agent,
    )
    if not out.ok:
        return _failure("task add", item, out)
    out = claim(repo, item, called_from=called_from, agent=agent)
    if not out.ok:
        return _failure("claim", item, out)
    wt = W.Worktree(
        item=item, path=Path(out.data["worktree"]), branch=out.data["branch"], base=out.data["base"]
    )
    if (bad := _stage(repo, wt, todo, message)) is not None:
        return _failure("commit", item, bad)

    _log, cfg, st = _load(repo, agent)
    who = (approved.row or {}).get("user", "?")
    note = (
        f"chore: {title}; {len(todo)} file(s) all match [chore].globs; standing approval "
        f"{CO.SUBJECT}@{CO.approval_digest(cfg)[:12]} by {who}"
    )
    skipped, gate = _skip_gates(repo, cfg, st, item, todo, note, agent)
    if isinstance(skipped, O.Outcome):
        return _failure(f"skip {gate}", item, skipped)
    _clear_primary(repo, todo)
    out = merge_item(repo, item, model=model, called_from=called_from, agent=agent)
    if not out.ok:
        _bring_back(repo, wt, todo)
        return _failure("merge", item, out)
    sha = out.data.get("sha", "")
    done = complete(repo, item, model=model, agent=agent, called_from=called_from)
    if not done.ok:
        return _failure("complete", item, done, sha=sha)
    return O.ok(
        KIND,
        item=item,
        sha=sha,
        files=todo,
        skipped_gates=skipped,
        approved_by=who,
        why=note,
        text=f"{item} landed as {sha[:8]}: {', '.join(todo)} (gates skipped: {', '.join(skipped)})",
    )
