"""Canonical commands for the approval-granting verbs an agent may run with a person's
approval (RA-consume-sites-2): `grant.add` and `chore.approve`.

Each binds the verb, the agent, what it acts on and the state it would change: a new grant is
a different state (the grants already recorded for the item and gate), so an approval to add
one round cannot be spent twice or after someone else added one."""

from __future__ import annotations

import json

from ...core.digest import content_digest
from .canon import Canon

GRANT = "grant.add"
CHORE = "chore.approve"


def _digest(record: dict) -> str:
    return content_digest(json.dumps(record, sort_keys=True, separators=(",", ":")))


def grant_canon(
    agent: str, *, item: str, gate: str, kind: str, count: int, when: str, existing: list[str]
) -> Canon:
    """Add ``count`` of ``kind`` on ``item``.``gate`` (``existing``: ids of the grants already
    recorded there, so a later grant is another state)."""
    return Canon(
        verb=GRANT,
        agent=agent,
        item=item,
        target=gate,
        args={"kind": kind, "count": str(count), "when": when},
        state=_digest({"existing": sorted(existing)}),
    )


def chore_canon(agent: str, *, list_digest: str) -> Canon:
    """Approve the CURRENT `[chore].globs` and `skip_gates` (``list_digest`` is their digest)."""
    return Canon(verb=CHORE, agent=agent, state=list_digest)
