# Approving an agent's gated command, remotely

An agent working in this project is refused some commands: the ones that override a safety check
(`gate skip` over a failed review, a forced `complete`, a forced `claim`, `approve partial`, trusting
a reviewer, and the like). You, the operator, can let the agent run one of them without sitting at
the host: you approve **that exact command**, once, and the agent re-runs it. This page is how, what
each way of approving proves, and where it is weaker than it looks. The design and the answers behind
it: [decisions/remote-approvals.md](decisions/remote-approvals.md),
[research/remote-approvals.md](research/remote-approvals.md).

## The flow

1. The agent runs the command and is refused. The refusal files a request (`[approvals].auto_request`,
   on by default), prints its id and names the route. An agent can also file one with
   `ddflow approvals request` (MCP: `ddflow_session action=ask`) or re-run with `--approval request`.
2. You see it. `ddflow approvals list` (also `pending`, `show ID`) prints what ddflow rendered: the
   canonical command, the state it applies to, the digest. The agent's own reason is quoted and cut
   (`reason_chars`); it is never part of what you approve.
3. You approve. At a terminal, over ssh included:
   `ddflow approvals approve ID --digest XXXXXXXX`, typing the first characters of the digest
   (`min_digest_chars`, default 8) so you cannot approve a request you did not read. Or in the agent's
   chat, below.
4. The agent re-runs the command with `--approval ID` (`ddflow wait --approval ID` blocks until you
   have answered). ddflow recomputes the digest from the current state and spends the approval.

An approval is bound to the verb, the item, the target, the arguments and the state of the gate or
item at request time. It is spent once (`uses`), expires if unanswered after `[agent].ask_ttl_s`
(24 hours) and if unspent after `use_ttl_s` (1 hour). A changed command, a changed state, another
verb, a denied, withdrawn, expired, revoked or already spent approval is refused, and the refusal
spends nothing. A successor agent may spend a predecessor's unspent approval: the digest binds the
action, not the actor. Take one back with `ddflow approvals revoke ID` while it is unspent.

**No agent can approve for you.** `approve`, `deny`, `answer` and `revoke` are refused under any agent
identity, there is no MCP tool for them, and an agent that appends an answer to the event log by hand
approves nothing beyond what the Limits section says.

## Levels: what an answer proves

| Level | Channel | What it shows |
|---|---|---|
| L1 | terminal or ssh `approvals approve` | a person at a shell, by the log's word |
| L2 | the agent's chat: you answer the `AskUserQuestion` the agent shows (`approvals ask ID`) or type `approve ID DIGEST`; the agent then runs `approvals attest ID` | the harness's transcript holds an answer; ddflow reads the transcript, never the agent's word |
| L3 | a one-time code over a sink, or a named GitHub login's review | the person got a message only they could read |
| L4 | an SSH signature over the canonical payload (`RA-channel-signed`, deferred until you provide a key) | a key ddflow never holds |

Each verb has a tier. **T1**: standing rules you switched on. **T2**: a person approves each instance
(L1, L2 or L3; the default for the generic acts above). **T3**: the approval system's own
configuration, command reviewers and credentials, which need L4. With no signer, T3 acts are
terminal-only, and over chat only if you opt in on this machine (`accept_chat_for_t3`, rated no
stronger than L2; `forbid_chat_for_t3` in any layer turns it off for good). `ddflow approvals log`
shows who asked, who answered, by which channel and level, what was spent and what looks wrong;
`ddflow gate list --overrides` and `ddflow doctor` surface the same.

## Standing rules (off by default)

`[approvals].policy` lists rules you switch on. Each is capped (`policy_max_uses` per item and
target), bounded by the standing window (`standing_window_s`, at most 24 hours, over
`standing_verbs`), recorded as `policy:<rule>` and never as you, and reported in `approvals log` and
`doctor`. They cover T2 only: a rule is never applied to a verb that needs more than L1.
`auto_approve_after_retries` answers the next filing after N refused ones and notifies you. Relayed
consent (an agent's `--yes --reason "the operator said so"`) still works for a few operations until
you list them in `require_approval`; each use is warned about.

## Limits, stated plainly

- **Same user, same host.** The event log is a file the agent's user can write. On one uid an agent
  can forge a terminal answer: it reaches L1 and no higher. ddflow refuses what needs more (T3,
  anything above L1), refuses answers with no recorded source, and never lets a later or duplicate
  event reopen a spent approval. The same-uid L1 forgery is visible, not impossible.
- **Chat approvals (L3) are forgeable** by an agent that holds the connector or `gh` token, and L2
  transcripts are writable by the agent's own user. They suit a solo operator who accepts that.
- **T3 is rated L1 on a single-uid host** until a signer is pinned outside the repository; doctor
  says "no approval signer pinned". Signing is deferred; no key work is done in this phase.
- **An agent that relays "the operator agreed" is not an approval.** Only a recorded answer is.
- Nothing here leaves the machine: no approval is sent to a service unless you configure a sink.

## Where to look

`ddflow approvals --help`, `ddflow prompts show approvals` (the route text agents read; edit a copy in
`.ddflow/prompts/approvals.md`), `[approvals]` in `ddflow config --explain`, and the adversarial tests
in `tests/test_approvals_e2e.py` and `tests/test_approvals_adversarial.py`.
