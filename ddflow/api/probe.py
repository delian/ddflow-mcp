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


def _text(p: PR.Probe, item: str, against: str, ref: str, asked: str) -> str:
    if p.result == PR.UNAVAILABLE:
        return (
            f"probe {item} against {against}: unavailable ({p.reason}); the commit-time check "
            f"decides. Retry: `ddflow probe {item} --against {asked}` "
            f"(MCP: ddflow_probe id={item} against={asked})"
        )
    if p.result == PR.CONFLICT:
        shown = ", ".join(p.paths) or "(git named no path)"
        if p.reason:
            shown += f" ({p.reason})"
        if p.method == PR.SCRATCH:
            shown += " (every file both sides changed; some may merge cleanly)"
        return (
            f"probe {item} against {against}: CONFLICT in {shown}. Merge {ref} into the "
            f"branch and resolve, or `ddflow wait` for the holder; re-probe with `ddflow probe {item} --against {asked}` "
            f"(MCP: ddflow_probe id={item} against={asked}). {PR.LIMIT}."
        )
    return f"probe {item} against {against}: clean ({p.method}). {PR.LIMIT}."


def probe(repo: Path, item: str, *, against: str = "", at: str = "", agent: str = "") -> O.Outcome:
    """Probe ``item``'s branch against ``against`` (default: the default branch, or an item id)."""
    log, cfg, st = _load(repo, agent)
    if at and at not in cfg.locks.probe_at:
        vs = f" --against {against}" if against else ""
        vs_mcp = f" against={against}" if against else ""
        why = (
            f"no probe at {at}: [locks].probe_at lists {', '.join(cfg.locks.probe_at) or 'nothing'}. "
            f"Probe anyway: `ddflow probe {item}{vs}` (MCP: ddflow_probe id={item}{vs_mcp})"
        )
        return O.nothing(KIND, why, id=item, result=PR.UNAVAILABLE, at=at, text=why)
    branch = _branch(st, cfg, item)
    if not branch:
        return O.failed(KIND, f"no such item {item!r}: `ddflow list` names the items")
    target = against or MAIN
    if target == MAIN:  # always the repository's default branch, even beside an item id "main"
        peer = W.default_branch(repo)
    elif target in st.items:
        peer = _branch(st, cfg, target)
    else:  # a branch name
        peer = target
    if not cfg.locks.probe:
        res = PR.Probe(PR.UNAVAILABLE, PR.NONE, reason="[locks].probe is off")
    else:
        res = PR.run(
            repo, branch, peer, timeout_s=cfg.locks.probe_timeout_s,
            min_git=cfg.locks.probe_merge_tree_min_git,
        )  # fmt: skip
    shown = peer if target == MAIN else target  # the ref actually probed
    data = {
        "id": item,
        "against": shown,
        "result": res.result,
        "method": res.method,
        "paths": list(res.paths),
        "head": res.item_sha,
        "base": res.against_sha,
        "why": res.reason,
        "text": _text(res, item, shown, peer, target),
    }
    log.append("probe.recorded", item, {k: v for k, v in data.items() if k not in ("id", "text")})
    if res.result == PR.CONFLICT:
        return O.failed(KIND, data["text"], **data)
    if res.result == PR.UNAVAILABLE:
        return O.nothing(KIND, data["text"], **data)
    return O.ok(KIND, **data)
