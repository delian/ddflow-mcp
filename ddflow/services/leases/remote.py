"""The remote claim ref: ``ddflow.services.leases`` re-exported helpers that take,
renew and drop the cross-clone claim ref under ``[flow].claims = 'remote'``.
"""

from __future__ import annotations

from ...config import Config
from ...infra import claimref as CR
from ...infra.log import EventLog
from .model import LeaseError


def _remote_take(log: EventLog, cfg: Config, item_id: str, holder: str, now: float) -> str:
    """Win the remote claim ref or refuse the claim. Returns "fresh" for a new ref and
    "renewed" when we already held it."""

    got = CR.take(log.root, cfg.flow.remote, item_id, holder, now + cfg.lease.ttl_s)
    if got.status == "held":
        raise LeaseError(
            f"{item_id} is claimed on the remote {cfg.flow.remote!r} by {got.holder or 'another clone'}"
            + (f" for another {max(0, got.expires - now):.0f}s" if got.expires else ""),
            holder=got.holder,
            item=item_id,
        )
    if got.status != "ok":
        raise LeaseError(
            f"{item_id}: [flow].claims = 'remote' but the remote claim could not be made "
            f"({got.detail or 'remote unavailable'}). Not claiming locally instead: that is "
            f"the silent split-brain this setting exists to prevent.",
            item=item_id,
        )
    return "renewed" if got.detail == "renewed" else "fresh"


def _remote_renew(log: EventLog, cfg: Config, item_id: str, holder: str, now: float) -> str:
    """Extend our remote claim. Returns the holder who has it instead of us ("" when ours,
    or when the remote could not be asked: it then lapses at its expiry and the next
    successful renew re-takes it)."""
    if cfg.flow.claims != "remote":
        return ""

    got = CR.renew(log.root, cfg.flow.remote, item_id, holder, now + cfg.lease.ttl_s)
    return (got.holder or "another clone") if got.status == "held" else ""


def _remote_drop(log: EventLog, item_id: str, holder: str) -> None:
    """Best effort: a ref we fail to delete lapses with its expiry and is replaced then."""

    cfg = Config.load(log.root)
    if cfg.flow.claims == "remote":
        CR.drop(log.root, cfg.flow.remote, item_id, holder)
