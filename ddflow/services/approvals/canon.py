"""The canonical command record an approval is of (D-RA-1, D-RA-2).

An approval is not of "something the agent described" but of a record ddflow builds from the
command it is about to run: the verb, the item and target, the agent that will run it, the
normalised arguments and a digest of the STATE it acts on (for a gate skip, the failed review
outcome and its findings). `digest()` is computed again when the agent re-runs the command, so
a widened argument, a different agent or a changed outcome is a different digest and needs a
new approval. The agent's own `reason` is never part of it: it is shown to the person quoted
and untrusted, and cannot change what is bound.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ...core.digest import content_digest
from . import sites

CANON_VERSION = 1


class UnknownVerb(ValueError):
    """A verb nobody inventoried (`sites.SITES`): it is not a gated command."""


@dataclass(frozen=True)
class Canon:
    verb: str
    agent: str
    item: str = ""
    target: str = ""
    #: Normalised arguments that change what the command does (a count, a path list), as strings.
    args: dict[str, str] = field(default_factory=dict)
    #: A digest of the state the command acts on, built by the site that gates it.
    state: str = ""

    def __post_init__(self) -> None:
        if self.verb not in sites.BY_VERB:
            raise UnknownVerb(f"{self.verb!r} is not a gated command (services/approvals/sites)")
        if not self.agent:
            raise ValueError("an approval is for an agent: name who will run the command")

    @property
    def site(self) -> sites.Site:
        return sites.BY_VERB[self.verb]

    def record(self) -> dict[str, Any]:
        return {
            "v": CANON_VERSION,
            "verb": self.verb,
            "agent": self.agent,
            "item": self.item,
            "target": self.target,
            "args": {str(k): str(v) for k, v in sorted(self.args.items())},
            "state": self.state,
        }

    def json(self) -> str:
        return json.dumps(self.record(), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return content_digest(self.json())

    def subject(self) -> str:
        """`cmd:<verb>:<item>:<target>`, the ask's subject (what `verdict` is asked about)."""
        return ":".join(("cmd", self.verb, self.item, self.target))
