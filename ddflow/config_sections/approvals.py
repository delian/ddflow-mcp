"""The `[approvals]` section: how an agent's request for an operator-gated command is filed
and how long an answer stays usable (P-remote-approvals, D-RA-Q9, D-RA-Q12).

Re-exported from `ddflow.config`, which assembles `Config` from every section. The request
window is `[agent].ask_ttl_s` (the ask engine's)."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("approvals")
@dataclass
class ApprovalsConfig:
    """Remote approvals: an operator-gated command an agent may run with a person's recorded,
    single-use, expiring approval (`ddflow approvals`)."""

    use_ttl_s: int = knob(
        3600,
        doc="How many seconds after a person answered an approval it can still be spent (default 3600, one hour; D-RA-Q9). A later use is refused and the agent files a new request. The window to ANSWER is `[agent].ask_ttl_s`.",
    )
    auto_request: bool = knob(
        True,
        doc="When true (default; D-RA-Q12), a gated command refused to an agent files the approval request itself and names its id in the refusal, so the operator sees exactly what to approve. When false the refusal only names the route and the agent files the request.",
    )
    reason_chars: int = knob(
        300,
        doc="How many characters of an agent's own reason are shown to the person in an approval request (default 300; the rest is cut with an ellipsis). The reason is quoted as untrusted text and is never part of what is approved.",
    )
    min_digest_chars: int = knob(
        8,
        doc="How many leading characters of a request's digest the person types back to approve or deny it (default 8): `ddflow approvals approve ID --digest XXXXXXXX`. It proves they read which request, so it cannot be answered blind.",
    )
