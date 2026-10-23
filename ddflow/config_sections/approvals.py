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
    accept_harness: bool = knob(
        True,
        doc="Accept an approval answered in the agent's own chat (default true; D-RA-Q5): the agent puts `ddflow approvals ask ID` to the person with AskUserQuestion and `ddflow approvals attest ID` reads the answer from the transcript the harness wrote, never from the agent's word. Tier T2 only. It cannot show that a person (not a script holding the session) answered or that the transcript was not edited by the agent's own user; `ddflow doctor` says so.",
    )
    transcript_dirs: list[str] = knob(
        factory=lambda: ["~/.claude/projects"],
        doc="Where the harness channel looks for session transcripts (`*.jsonl`) that hold the answer to an approval question (default ~/.claude/projects). Only files modified since the request was filed are read.",
    )
    expiry_warn_s: int = knob(
        600,
        doc="How many seconds before the end of its use window `ddflow approvals log` starts to say an approved-but-unspent request will expire unused (default 600).",
    )
    policy: list[str] = knob(
        factory=list,
        doc="Standing auto-approval rules switched ON (default none; D-RA-Q8, D-RA-4). Each id names a rule registered in code (`ddflow approvals log` lists what fired): `skip_after_unavailable` (an agent may skip a failed review gate whose reviewer was recorded unavailable `policy_unavailable_runs` times), `accept_baseline_red` (an agent may accept a partial that ddflow measured baseline-red). A rule approves only its own verb, at most `policy_max_uses` times per item and target, and every use is recorded as an approval answered by `policy:<id>`, never by a person. An agent surface cannot write this knob.",
    )
    policy_max_uses: int = knob(
        1,
        doc="How many times one standing rule may approve the same item and target (default 1). A rule past its cap falls back to asking a person.",
    )
    policy_unavailable_runs: int = knob(
        3,
        doc="How many recorded `unavailable` runs of a review gate the rule `skip_after_unavailable` needs before it lets an agent skip that gate's failure (default 3).",
    )
