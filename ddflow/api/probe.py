"""`ddflow probe ITEM [--against main|ITEM]`: can this item's branch merge textually clean?

Resolves the two refs, runs `services.probe` and records the answer as `probe.recorded` (a
probe that could not run is recorded `unavailable`, never `clean`). Exit 0 clean, 1 conflict
(the paths are named), 2 unavailable."""

from __future__ import annotations

from pathlib import Path

from ..core import flow as F
from ..core import outcome as O
from ..infra import worktree as W
from ..services import probe as PR
from ._base import _load

KIND = "probe"
MAIN = "main"


def _branch(st, cfg, item_id: str) -> str:
    it = st.items.get(item_id)
    if it is None:
        return ""
    return it.lease.branch if it.lease and it.lease.branch else F.branch_name(it, cfg)


def _text(p: PR.Probe, item: str, against: str) -> str:
    if p.result == PR.UNAVAILABLE:
        return (
            f"probe {item} against {against}: unavailable ({p.reason}); the commit-time check "
            f"decides. Retry: `ddflow probe {item} --against {against}` "
            f"(MCP: ddflow_probe id={item} against={against})"
        )
    if p.result == PR.CONFLICT:
        shown = ", ".join(p.paths) or "(git named no path)"
        return (
            f"probe {item} against {against}: CONFLICT in {shown}. Merge {against} into the "
            f"branch and resolve, or `ddflow wait` for the holder. {PR.LIMIT}."
        )
    return f"probe {item} against {against}: clean ({p.method}). {PR.LIMIT}."


def probe(repo: Path, item: str, *, against: str = "", agent: str = "") -> O.Outcome:
    """Probe ``item``'s branch against ``against`` (default: the default branch, or an item id)."""
    log, cfg, st = _load(repo, agent)
    branch = _branch(st, cfg, item)
    if not branch:
        return O.failed(KIND, f"no such item {item!r}: `ddflow list` names the items", id=item)
    target = against or MAIN
    if target in st.items:
        peer = _branch(st, cfg, target)
    else:  # a branch name; "main" means the repository's default branch
        peer = W.default_branch(repo) if target == MAIN else target
    if not cfg.locks.probe:
        res = PR.Probe(PR.UNAVAILABLE, PR.NONE, reason="[locks].probe is off")
    else:
        res = PR.run(
            repo, branch, peer, timeout_s=cfg.locks.probe_timeout_s,
            min_git=cfg.locks.probe_merge_tree_min_git,
        )  # fmt: skip
    data = {
        "id": item,
        "against": target,
        "result": res.result,
        "method": res.method,
        "paths": list(res.paths),
        "head": res.item_sha,
        "base": res.against_sha,
        "why": res.reason,
        "text": _text(res, item, target),
    }
    log.append("probe.recorded", item, {k: v for k, v in data.items() if k not in ("id", "text")})
    if res.result == PR.CONFLICT:
        return O.failed(KIND, data["text"], **data)
    if res.result == PR.UNAVAILABLE:
        return O.nothing(KIND, data["text"], **data)
    return O.ok(KIND, **data)
