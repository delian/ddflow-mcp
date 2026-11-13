"""The `[approvals]` section: how an agent's request for an operator-gated command is filed
and how long an answer stays usable (P-remote-approvals, D-RA-Q9, D-RA-Q12).

Re-exported from `ddflow.config`, which assembles `Config` from every section. The request
window is `[agent].ask_ttl_s` (the ask engine's)."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob

#: The standing auto-approve window's ceiling: `standing_window_s` above it is refused.
STANDING_WINDOW_MAX_S = 24 * 3600

#: The retry-threshold's ceiling: `auto_approve_after_retries` above it is refused.
AUTO_APPROVE_RETRIES_MAX = 1000


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
        factory=list,
        doc="Extra session-transcript paths the harness channel reads for an approval answer, added to each harness descriptor's `[transcripts].root` (the directories of session transcripts and SQLite stores the harness writes; Kilo's default store is read when it exists). Only transcripts modified since the request was filed are read.",
    )
    expiry_warn_s: int = knob(
        600,
        doc="How many seconds before the end of its use window `ddflow approvals log` starts to say an approved-but-unspent request will expire unused (default 600).",
    )
    policy: list[str] = knob(
        factory=list,
        doc="Standing auto-approval rules switched ON (default none; D-RA-Q8, D-RA-4). Each id names a rule registered in code (`ddflow approvals log` lists what fired): `skip_after_unavailable` (an agent may skip a failed review gate whose reviewer was recorded unavailable `policy_unavailable_runs` times), `accept_baseline_red` (an agent may accept a partial that ddflow measured baseline-red), `complete_unavailable` (an agent may complete an item whose unmet condition is an unavailable tool/gate) and `claim_unavailable` (an agent may claim an item held by a vanished holder). Every rule auto-approves only while the standing window `standing_window_s` is open, at most `policy_max_uses` times per item and target, and every use is recorded as an approval answered by `policy:<id>`, never by a person. An agent's edit of this knob is a `config.set`, which needs a person's approval.",
    )
    policy_max_uses: int = knob(
        1,
        doc="How many times one standing rule may approve the SAME item and target (default 1): the cap is counted per item+target, so a backlog of DISTINCT items is not limited by it -- each item gets its own use, and a completed item needs no more approvals (the standing `complete_unavailable` use completes it once and it is done). A rule past its cap falls back to asking a person. An explicit `--approval request` is ALWAYS reserved for a person (never answered by a standing rule, never counted against this cap), whatever the window or the cap say.",
    )
    policy_unavailable_runs: int = knob(
        3,
        doc="How many recorded `unavailable` runs of a review gate the rule `skip_after_unavailable` needs before it lets an agent skip that gate's failure (default 3).",
    )
    auto_approve_after_retries: int = knob(
        0,
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= AUTO_APPROVE_RETRIES_MAX
            else f"must be an integer between 0 and {AUTO_APPROVE_RETRIES_MAX} (0 turns it off)"
        ),
        doc="Retry threshold (default 0 = off): after the SAME generic T2 verb (`complete.force`, `claim.force`, `gate.skip.failed_review`, `approve.partial`) has been filed and refused this many times for the same item -- each time the operator was asked and did not answer, counted across EVERY agent -- ddflow AUTO-ANSWERS the next filing as a standing rule recorded `policy:auto-after-retries` (the request's digest preserved) and NOTIFIES the operator, surfaced in `ddflow approvals log` and `ddflow doctor`, rather than asking a person again. Distinct from `standing_window_s`, which is TIME-based and carries no per-use notification: this is COUNT-based and always notifies. Not gated by the standing window nor `policy_max_uses`: the threshold is the whole condition, and every auto-answer above it is on the record so `doctor` and `approvals log` show each one. Once armed, it stays armed: the refusals are asks the log keeps for its life (an expired ask was still asked and left unanswered), so later filings of that verb for that item keep auto-approving with a notification until the operator lowers this knob. 0 turns it off. An agent's edit of this knob is a `config.set`, which needs a person's approval.",
    )
    standing_window_s: int = knob(
        3600,
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= STANDING_WINDOW_MAX_S
            else f"must be an integer between 1 and {STANDING_WINDOW_MAX_S} (24 hours)"
        ),
        doc="How long, in seconds, the STANDING AUTO-APPROVE WINDOW stays open (default 3600, one hour; capped at 86400, 24 hours -- a value above it is refused). The operator pre-authorises the generic T2 class for this bounded time so overnight pile-ups proceed unattended: every standing rule (`skip_after_unavailable`, `accept_baseline_red`, `complete_unavailable`, `claim_unavailable`) auto-approves only while the window is outstanding, and the instant it lapses auto-approval stops and a person is asked again. `standing_verbs` scopes which generic verbs the window covers. An agent's edit of this knob is a `config.set`, which needs a person's approval.",
    )
    standing_verbs: list[str] = knob(
        factory=lambda: [
            "complete.force",
            "claim.force",
            "gate.skip.failed_review",
            "approve.partial",
        ],
        doc="The generic T2 verbs the standing window pre-authorises (default the whole generic T2 class: complete.force, claim.force, gate.skip.failed_review, approve.partial). Scopes which verbs a standing rule may cover: a standing rule whose verb is not listed never fires. Every standing rule is window-gated -- `skip_after_unavailable`, `accept_baseline_red`, `complete_unavailable` and `claim_unavailable` all fire only while the window is outstanding, so auto-approval of the whole class stops the instant it lapses. A non-generic site has no standing rule, so listing one of its verbs here does nothing. An agent's edit of this knob is a `config.set`, which needs a person's approval.",
    )
    accept_chat_for_t3: bool = knob(
        False,
        doc="Let a person approve a tier-3 act (such as trusting a command reviewer) by TYPING `approve ID DIGEST` in the agent's chat (default false; D-RA-T3-chat). Honest rating: no stronger than L2 -- the transcript is writable by the agent's own user, so this suits a solo operator who accepts that; every use is recorded as an approval of grade `L2-typed` and `ddflow approvals log` and `doctor` warn on each. An AskUserQuestion answer never satisfies a tier-3 act. Read ONLY from the machine-local layer (`.ddflow/local/config.toml`): the committed file and the environment cannot turn it on, and no agent surface may write it or any chat answer enable it. It cannot be switched on remotely the first time; the operator sets it on the host.",
    )
    forbid_chat_for_t3: bool = knob(
        False,
        doc="Forbid tier-3 acts over typed chat whatever `accept_chat_for_t3` says (default false). Tighten-only: true in any layer (a project or company file, the machine file, the environment) wins over the opt-in; nothing a chat answer says can undo it.",
    )
    require_approval: list[str] = knob(
        factory=list,
        doc="Verbs whose RELAYED consent (an agent's `--yes --reason`, 'the operator said so in chat') is no longer accepted (default none; D-RA-Q2). `upgrade.self` and `plan.confirm` may be listed: an agent then needs `--approval ID`, a person's recorded approval. Until a verb is listed, a relayed use still works and every one is warned about by `ddflow approvals log` and `doctor`. `skew.override` cannot be listed: the version guard refuses the approval engine's own writes, so it has no approval route. A person at a terminal is unaffected. An agent's edit of this knob is a `config.set`, which needs a person's approval.",
    )
