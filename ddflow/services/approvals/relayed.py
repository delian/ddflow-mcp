"""Relayed consent next to the approval engine (RA-consume-sites-4, D-RA-Q2).

`upgrade --self`, `plan confirm` and the skew override take the operator's consent as free text
(`--yes --reason`, "the operator said so in chat"). D-RA-Q2 keeps that for ONE more release as a
stated exception, so for these verbs an AGENT now has three ways in, and `gate` is the one place
that tells them apart:

* a recorded person's approval (`--approval ID`): the same single-use, digest-bound spend every
  other gated command takes (`consume.operator_gate`); it stands for the consent;
* relayed consent: allowed while the verb is not in `[approvals].require_approval`, and every use
  leaves `RELAYED_TAG` in the reason the effect event records, which `uses` reads back so the
  audit view (and `doctor`) warns on EACH one;
* neither: refused, with the approval request filed and its id named.

A person at a terminal is unchanged. Nothing here answers an approval or widens one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ...config import Config
from ..identity import is_agent
from . import consume as CN
from .canon import Canon

RELAYED_TAG = "[relayed consent] "
#: How much of an agent's own words a warning quotes.
QUOTE = 80


@dataclass(frozen=True)
class Verdict:
    ok: bool
    #: Why not (the route an agent is told), "" when ok or when a person gave no consent (the
    #: verb's own refusal says what is missing).
    message: str = ""
    #: The reason the effect records: the person's own, an approval's, or a tagged relayed one.
    reason: str = ""
    #: "person", "approval" or "relayed".
    via: str = ""


def _approved(stamp: dict[str, Any]) -> str:
    return (
        f"approval {stamp['ask']} by {stamp['approved_by']} via {stamp['via']} (L{stamp['level']})"
    )


def gate(  # noqa: PLR0913 -- one keyword per facet of the canonical command
    log,
    cfg: Config,
    verb: str,
    *,
    requested_agent: str = "",
    via_mcp: bool = False,
    approval: str = "",
    consented: bool,
    reason: str = "",
    item: str = "",
    target: str = "",
    state: str = "",
) -> Verdict:
    """May this call act? ``consented``: the caller passed the relayed consent flags.

    Call it only once the command is known valid, so no approval is spent on one that was not."""
    if not is_agent(requested_agent, via_mcp):  # a person: unchanged, the verb's own consent rule
        return Verdict(consented, reason=reason, via="person")
    canon = Canon(verb=verb, agent=log.agent_id, item=item, target=target, state=state)
    if approval or not consented or verb in cfg.approvals.require_approval:
        d = CN.operator_gate(log, cfg, canon, approval_id=approval, reason=reason)
        if not d.ok:
            return Verdict(False, d.message)
        return Verdict(True, reason=_approved(d.stamp), via="approval")
    return Verdict(True, reason=RELAYED_TAG + reason, via="relayed")


def _quote(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= QUOTE else text[: QUOTE - 3] + "..."


def uses(events: Iterable[Any], state: Any) -> list[str]:
    """One warning per relayed use: the events that carry `RELAYED_TAG` and every skew override
    (a free-text consent by construction; it has no approval route, see the `skew.override` row of `sites`)."""
    out = []
    for ev in events:
        d = ev.data
        if ev.kind == "upgrade.applied":
            text = " ".join(str(v) for v in (d.get("confirmed") or {}).values())
            verb = "upgrade.self"
        elif ev.kind == "item.unblocked":
            text, verb = str(d.get("note", "")), "plan.confirm"
        else:
            continue
        if RELAYED_TAG in text:
            out.append(
                f"{verb} by {ev.agent} at {ev.ts[:19]} ran on RELAYED consent (free text, not a "
                f"recorded approval; D-RA-Q2 keeps it one release): "
                f"{_quote(text.split(RELAYED_TAG, 1)[1])!r}"
            )
    out += [
        f"skew.override by {o['agent']} at {str(o['at'])[:19]} ran on relayed consent "
        f"(the guard blocks the approval engine's own writes, so it has no approval route): "
        f"{_quote(o['reason'])!r}"
        for o in getattr(state, "skew_overrides", [])
    ]
    return out
