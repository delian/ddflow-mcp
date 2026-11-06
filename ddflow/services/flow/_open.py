"""PR flow: opening a request.

Part of the former ``ddflow/services/flow.py``, split into submodules that stay under
30k chars each (rule r-small-files); see the package docstring for the full story.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from ...config import Config
from ...core import flow as F
from ...core.model import GateRecord, Item, State
from ...infra import forge as FG
from ...infra import worktree as W
from ...infra.log import EventLog
from .. import choices as CH
from .. import completion as CM
from .. import leases as L
from ._core import _state, stacked_on, target


@dataclass
class Opened:
    item: str
    ok: bool = False
    refused: bool = False
    unavailable: bool = False
    reason: str = ""
    number: int = 0
    url: str = ""
    base: str = ""
    head: str = ""
    stacked_on: str = ""
    created: bool = False
    warnings: list[str] = field(default_factory=list)


def pre_merge_blockers(
    st: State, cfg: Config, item_id: str, *, repo: Path, model: str
) -> list[str]:
    """What would stop this item completing ONCE MERGED. Asked before a request opens.

    A request is where a person's time goes. Opening one for work whose own pipeline is
    unfinished spends that time on something ddflow would then refuse to complete -- and
    the refusal arrives after the merge, when it can no longer stop anything. So the
    verdict is computed as if the merge gate had passed, and anything left is a reason
    not to ask anyone yet. A branch not merged yet is no such reason: the request is what
    merges it.
    """
    hypo = copy.deepcopy(st)
    it = hypo.items[item_id]
    it.gates["merge"] = GateRecord(gate="merge", outcome="passed")
    blockers = CM.verdict(hypo, cfg, item_id, repo=repo, model=model).blockers
    return [b for b in blockers if CM.UNMERGED_BLOCKER not in b]


def pr_body(st: State, it: Item, *, on: Item | None) -> str:
    lines = [it.body.strip() or it.title, ""]
    if on is not None and on.pr:
        lines += [
            f"**Stacked on {on.id}** ({on.pr.url}). Review that first; this request "
            f"is retargeted automatically once it merges.",
            "",
        ]
    ran = [(g, r.outcome) for g, r in it.gates.items() if r.outcome]
    if ran:
        lines += ["| gate | outcome |", "|---|---|"]
        lines += [f"| {g} | {o} |" for g, o in ran]
        lines.append("")
    lines.append(f"Item: {it.id}")
    return "\n".join(lines)


def open_request(
    repo: Path,
    cfg: Config,
    log: EventLog,
    item_id: str,
    *,
    title: str = "",
    model: str = "",
) -> Opened:
    st = _state(log)
    it = st.items[item_id]
    out = Opened(item=item_id, head=it.branch)
    bad = F.problems(cfg)
    if bad:
        out.refused, out.reason = True, "; ".join(bad)
        return out
    blockers = pre_merge_blockers(st, cfg, item_id, repo=repo, model=model)
    if blockers:
        out.refused = True
        out.reason = (
            f"not opening a request for {item_id}: it could not complete even once "
            f"merged, so a reviewer's time would be spent on something ddflow would then "
            f"refuse:\n" + "\n".join(f"  - {b}" for b in blockers)
        )
        return out
    try:
        forge = FG.detect(repo, cfg)
    except FG.ForgeUnavailable as exc:
        out.unavailable, out.reason = True, str(exc)
        return out
    on = stacked_on(st, it)
    out.base = on.branch if on is not None else target(repo, cfg, it, st)
    out.stacked_on = on.id if on is not None else ""
    path = W.load_path(repo, it.worktree)
    pushed = W.push(path, cfg.flow.remote, it.branch)
    if not pushed.ok:
        out.refused = True
        out.reason = (
            f"push of {it.branch} to {cfg.flow.remote} was rejected: {pushed.err or pushed.out}\n"
            f"If someone committed to the branch on the forge, `git -C {path} pull` and "
            f"merge again. ddflow never force-pushes."
        )
        return out
    try:
        info = forge.find(it.branch)
        if info is not None and info.state == "open":
            if info.base != out.base:
                forge.set_base(info.number, out.base)
                info = forge.view(info.number)
        else:
            info = forge.create(
                head=it.branch,
                base=out.base,
                title=title or f"{it.id}: {it.title}",
                body=pr_body(st, it, on=on),
                draft=cfg.flow.pr_draft,
                labels=list(cfg.flow.pr_labels),
                reviewers=list(cfg.flow.pr_reviewers),
            )
            out.created = True
    except FG.ForgeUnavailable as exc:
        out.unavailable, out.reason = True, str(exc)
        return out
    except FG.ForgeError as exc:
        out.refused, out.reason = True, str(exc)
        return out
    data = info.event_data(forge.name)
    data.update(kind=it.kind, author_model=model, review="pending")
    log.append("pr.opened", item_id, data)
    L.release(log, item_id, note=f"in review: {info.url}")
    # The first request is where review policy starts to matter: record whatever was not
    # chosen, so it is followed from here rather than re-decided by the next ddflow.

    CH.adopt_defaults(log, cfg, ["pr_merge", "on_changes_requested", "stack"])
    out.ok, out.number, out.url = True, info.number, info.url
    if cfg.flow.pr_merge == "auto" and on is None:
        try:
            forge.merge(
                info.number,
                strategy=cfg.worktree.merge_strategy,
                head_sha=info.head_sha,
                auto=True,
            )
        except (FG.ForgeError, FG.ForgeUnavailable) as exc:
            out.warnings.append(
                f"auto-merge could not be requested ({exc}); `pr sync` will still "
                f"complete the item once a person merges it."
            )
    return out
