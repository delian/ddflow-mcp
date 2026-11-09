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
    agent: str = "",
) -> O.Outcome:
    """Send ``--commit``/``--diff``/``--blob`` to ``item``'s holder and report the ref."""
    log, cfg, st = _load(repo, agent)
    to_agent = _holder_agent(st, item)
    if not to_agent:
        return O.failed("handoff.suggest", f"{item} has no live lease holder to suggest to")
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
            from_item="",
            base=tip,
        )
    except send.SuggestError as exc:
        return O.failed("handoff.suggest", str(exc))
    apply_txt = (
        f"ddflow suggest apply {out.ref}"
        if out.ref
        else "ddflow apply via patch-id (no base: blob-only suggestion)"
    )
    text = (
        f"suggested to {to_agent} on {item}\n"
        f"probe: {out.probe}\n"
        f"ref: {out.ref or '(no base: blob stored)'}\n"
        f"blob: {out.blob}\n"
        f"owner: {apply_txt}"
    )
    return O.ok("handoff.suggest", ref=out.ref, probe=out.probe, blob=out.blob, text=text)
