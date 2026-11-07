"""The canonical command record an approval is of (D-RA-1, D-RA-2).

An approval is not of "something the agent described" but of a record ddflow builds from the
command it is about to run: the verb, the item and target, the normalised arguments and a digest
of the STATE it acts on (for a gate skip, the failed review outcome and its findings).
`digest()` is computed again when the agent re-runs the command, so
a widened argument or a changed outcome is a different digest and needs a new approval. The
agent is NOT part of the digest: a successor lane re-running the SAME verb on the same item,
target and state may spend the approval (B86be969daf), so a dead agent does not orphan a
granted approval. The agent's own `reason` is never part of it: it is shown to the person quoted
and untrusted, and cannot change what is bound.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...core.digest import content_digest
from . import sites


class UnknownVerb(ValueError):
    """A verb nobody inventoried (`sites.SITES`): it is not a gated command."""


@dataclass(frozen=True)
class Canon:
    verb: str
    agent: str
    item: str = ""
    target: str = ""
    #: Normalised arguments that change what the command does (a count, a path list), as strings.
    args: Mapping[str, Any] | tuple[tuple[str, str], ...] = ()
    #: A digest of the state the command acts on, built by the site that gates it.
    state: str = ""

    def __post_init__(self) -> None:
        # frozen means frozen: keep the arguments as sorted string pairs, not the caller's dict
        pairs = self.args.items() if isinstance(self.args, Mapping) else self.args
        norm = tuple(sorted((str(k), str(v)) for k, v in pairs))
        if len({k for k, _ in norm}) != len(norm):
            raise ValueError("an argument is named twice: the digest could not show both")
        object.__setattr__(self, "args", norm)
        if self.verb not in sites.BY_VERB:
            raise UnknownVerb(f"{self.verb!r} is not a gated command (services/approvals/sites)")
        if not self.agent:
            raise ValueError("an approval is for an agent: name who will run the command")

    @property
    def site(self) -> sites.Site:
        return sites.BY_VERB[self.verb]

    def record(self) -> dict[str, Any]:
        return {
            "v": 1,
            "verb": self.verb,
            "item": self.item,
            "target": self.target,
            "args": dict(self.args),
            "state": self.state,
        }

    def record_named(self, agent: str) -> dict[str, Any]:
        """The legacy v1 record that signed the requesting agent in the digest (pre-B86be969daf).
        Kept only so a pre-existing, still-unspent approval -- whose stored digest was computed
        over ``{... "agent": <requester>}`` -- stays spendable after the actor leaves the digest."""
        rec = self.record()
        rec["agent"] = agent
        return rec

    def json(self) -> str:
        return json.dumps(self.record(), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return content_digest(self.json())

    def digest_named(self, agent: str) -> str:
        payload = json.dumps(self.record_named(agent), sort_keys=True, separators=(",", ":"))
        return content_digest(payload)

    def subject(self) -> str:
        """`cmd:<verb>:<item>:<target>`, the ask's subject (what `verdict` is asked about)."""
        esc = (
            p.replace("%", "%25").replace(":", "%3A") for p in (self.verb, self.item, self.target)
        )
        return "cmd:" + ":".join(esc)
