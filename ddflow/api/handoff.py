"""`ddflow suggest`: send a bounded change to the lease holder of an item, as a suggestion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..adapters import git
from ..core import outcome as O
from ..services.handoff import send
from ._base import _load


def _holder_agent(st: Any, item: str) -> str:
    it = getattr(st, "items", {}).get(item)
    lease = getattr(it, "lease", None) if it is not None else None
    return getattr(lease, "holder", "") if lease is not None else ""


def _owner_tip(repo: Path, item: str, st: Any) -> str:
    """The owner's current tip: their branch ref, else the item's base, else "" (no ref)."""
    it = getattr(st, "items", {}).get(item)
    ref = f"refs/heads/ddflow/{item}"
    r = git.run(repo, "rev-parse", "--verify", "--quiet", ref)
    if r.ok:
        return r.out.strip()
    return getattr(it, "base", "") if it is not None else ""


def suggest(
    repo: Path,
    *,
    item: str,
    reason: str,
    kind: str = "",
    commit: str = "",
    diff: str = "",
    blob: str = "",
    evidence: str = "",
    agent: str = "",
) -> O.Outcome:
    """Send ``--commit``/``--diff``/``--blob`` to ``item``'s holder and report the ref."""
    log, cfg, st = _load(repo, agent)
    to_agent = _holder_agent(st, item)
    if not to_agent:
        return O.failed(
            "handoff.suggest",
            f"{item} has no live lease holder to suggest to; wait for the lease "
            f"(CLI `ddflow wait --item {item}`, MCP ddflow_wait item={item})",
        )
    tip = _owner_tip(repo, item, st)
    try:
        out = send.suggest(
            log,
            cfg,
            to_item=item,
            to_agent=to_agent,
            reason=reason,
            kind=kind,
            commit=commit,
            diff=diff,
            blob=blob,
            evidence=evidence,
            from_item="",
            base=tip,
        )
    except send.SuggestError as exc:
        return O.failed("handoff.suggest", str(exc))
    sid = out.id
    nxt = (
        f"apply:  ddflow suggest apply {sid}   (MCP ddflow_suggest action=apply id={sid})\n"
        f"reject: ddflow suggest reject {sid}  (MCP ddflow_suggest action=reject id={sid})\n"
        f"show:   ddflow suggest show {sid}    (MCP ddflow_suggest action=show id={sid})\n"
        f"expiry: ddflow suggest expire {sid}  (MCP ddflow_suggest action=expire id={sid})"
    )
    text = (
        f"suggested to {to_agent} on {item}\n"
        f"probe: {out.probe}\n"
        f"ref: {out.ref or '(no base: blob stored)'}\n"
        f"blob: {out.blob}\n"
        f"owner next:\n{nxt}"
    )
    return O.ok("handoff.suggest", id=sid, ref=out.ref, probe=out.probe, blob=out.blob, text=text)
