"""`decide`: close a decide task from the decision record (DX-decide-claim).

A decide task owns the decision document's path but used to be abandoned "done as a
decision" with the document unwritten. This verb checks the record exists, writes
`docs/ddflow/decisions/<slug>.md` from it in the task's worktree, commits it, records the
remaining gates skipped (the record is the operator's own words), merges and completes.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core import outcome as O
from ..core.autoorder import is_decide
from ..core.globs import inside
from ..infra import worktree as W
from ..infra.fsio import replace_text
from ..services import chore as CO
from ._base import _load
from .chore import _skip_gates as skip_chore_gates
from .lifecycle import complete
from .lifecycle import merge as merge_item

KIND = "item.decided"
DOC_DIR = "docs/ddflow/decisions"


def slug_of(decision_id: str) -> str:
    """`D-acp-r2` -> `acp`: the id without its `D-` prefix and `-rN` revision suffix."""
    return re.sub(r"-r\d+$", "", re.sub(r"^D-", "", decision_id))


def render(item: str, d) -> str:
    """The decision document: the record restated, the log staying the source of truth."""
    head = f"{d.status} · decided by {d.decided_by or 'unrecorded'}"
    if d.by:
        head += f" · recorded by {d.by}"
    if d.supersedes:
        head += f" · supersedes {', '.join(d.supersedes)}"
    parts = [
        f"# {d.title or d.id}",
        f"Decision document for the task `{item}`. It restates the decision record below; "
        f"the record in the event log is the source of truth (`ddflow decision show {d.id}`).",
        f"## {d.id}: {d.title}",
        f"status **{d.status}**{head[len(d.status) :]}",
    ]
    for label, text in (
        ("Context", d.context),
        ("Decision", d.decision),
        ("Consequences", d.consequences),
        ("Alternatives rejected", d.alternatives),
    ):
        if text:
            parts.append(f"**{label}.** {text}")
    if d.globs:
        parts.append("Governs: " + ", ".join(f"`{g}`" for g in d.globs))
    return "\n\n".join(parts) + "\n"


def _fail(stage: str, item: str, out: O.Outcome, **data) -> O.Outcome:
    return O.Outcome(
        kind=KIND,
        data={"item": item, "stage": stage, **data},
        exit=out.exit,
        reason=f"decide {item}: {stage} failed: {out.reason} (the task stays open: "
        f"`ddflow show {item}`)",
    )


def _check(repo, cfg, st, item: str, decision_id: str, path: str):
    """(item, decision, doc path), or the refusal."""
    it = st.items.get(item)
    if it is None or it.removed:
        return O.failed(KIND, f"no such item {item!r}", item=item)
    if not is_decide(it.tags):
        return O.refused(
            KIND,
            f"{item} is not a decide task (tag it `decide`): other work takes the normal pipeline",
            item=item,
        )
    d = st.decisions.get(decision_id)
    if d is None:
        return O.failed(
            KIND,
            f"no decision {decision_id!r}: record it first (`ddflow decision add --id "
            f"{decision_id} ...`), then decide {item}",
            item=item,
        )
    if not d.live:
        return O.refused(KIND, f"{decision_id} is not in force ({d.status})", item=item)
    if not it.lease or not it.worktree:
        return O.refused(KIND, f"claim {item} first: the document is written under its lease")
    rel = path or f"{DOC_DIR}/{slug_of(decision_id)}.md"
    if not any(inside(rel, g) for g in it.globs):
        return O.refused(
            KIND,
            f"{rel} is outside {item}'s globs: `ddflow update {item} --globs "
            f'"{",".join([*it.globs, rel])}"` first, or pass --path',
            item=item,
        )
    refusal = _chore_class(cfg, st, item, rel)
    return (it, d, rel) if refusal is None else refusal


def _chore_class(cfg, st, item: str, rel: str) -> O.Outcome | None:
    """The refusal unless ``rel`` is in the approved chore class."""
    if CO.outside(cfg, [rel]):
        return O.refused(
            KIND,
            f"{rel} matches no [chore].globs ({', '.join(cfg.chore.globs)}): a decision "
            f"document lands under the chore class; widen the list (a person approves it) "
            f"or use the normal pipeline",
            item=item,
        )
    approved = CO.standing(st, cfg)
    if not approved.ok:
        return O.refused(
            KIND,
            f"{approved.reason}: a decision document lands under the approved chore list "
            f"([chore].globs); a person runs `ddflow chore approve`",
            item=item,
        )
    return None


def decide(
    repo: Path,
    item: str,
    *,
    decision_id: str,
    path: str = "",
    model: str = "",
    agent: str = "",
    called_from: Path | None = None,
) -> O.Outcome:
    """Write the document for ``decision_id``, then land and complete decide task ``item``."""
    _log, cfg, st = _load(repo, agent)
    got = _check(repo, cfg, st, item, decision_id, path)
    if isinstance(got, O.Outcome):
        return got
    it, d, rel = got
    wt = W.Worktree(item=item, path=W.load_path(repo, it.worktree), branch=it.branch, base=it.base)
    target = wt.path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    replace_text(target, render(item, d))
    made = W.commit(wt, f"decision {decision_id}: {d.title}"[:120], [rel])
    if not made.ok and "nothing to commit" not in (made.out + made.err):
        return O.failed(KIND, (made.err or made.out).strip(), item=item)
    note = (
        f"chore: decide {decision_id}; {rel} restates the record; standing approval "
        f"{CO.SUBJECT}@{CO.approval_digest(cfg)[:12]}"
    )
    skipped, gate = skip_chore_gates(repo, cfg, st, item, [rel], note, agent)
    if isinstance(skipped, O.Outcome):
        return _fail(f"skip {gate}", item, skipped)
    out = merge_item(repo, item, model=model, called_from=called_from, agent=agent)
    if not out.ok:
        return _fail("merge", item, out)
    sha = out.data.get("sha", "")
    done = complete(repo, item, model=model, agent=agent, called_from=called_from)
    if not done.ok:
        return _fail("complete", item, done, sha=sha)
    return O.ok(
        KIND,
        item=item,
        decision=decision_id,
        path=rel,
        sha=sha,
        skipped_gates=skipped,
        text=f"{item} decided by {decision_id}: {rel} landed as {sha[:8]}",
    )
