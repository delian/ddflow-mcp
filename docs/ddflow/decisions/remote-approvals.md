# Remote approvals: decision proposals

Status: **PROPOSAL 2026-10-22**, not decided. Task `RA-research`; the operator's answers are collected by `RA-decide`
(lead asks, records each as `D-RA-Q*`). Evidence: `docs/ddflow/research/remote-approvals.md` (section numbers refer to
it). Phase: `P-remote-approvals`. Governing records: `D-shared-engines-r2` (1: one approvals engine),
`D-remote`, `D-reviewer-trust-r2`, `D-failed-review-gate`, `D-review-force-with-reason`, `D-CM-7`/`D-CM-Q4Q5`
(never hold keys), `D-mcp-tool-families` (no new tool), `D-config-layer-order`, `D-org-config`,
`r-everything-configurable`, `r-company-workflows`.

Operator request (2026-10-22, verbatim): "however, we want the operator to be able to manage everything in this
project remotely and as much automated as possible and safe, so such commands should be possible to be executed by the
agent with operator's permission".

## D-RA-1 One protocol: request, approve, consume, on the existing ask engine

An operator-gated command, refused to an agent today, becomes runnable by that agent **with a recorded, single-use,
expiring approval bound to the exact command**. The agent files a request (the refusal site files it automatically);
the operator answers out of band; the agent re-runs the command with `--approval <id>` and ddflow recomputes the
digest from current state, checks level, requester, expiry and uses, spends one use under the log lock and stamps the
effect with the approval. No second engine: `services/ask` is extended (proof levels, `ask.consumed`), `services/approval`
`check` and the gate-accept path read its verdict. The agent identity still cannot answer, mark an ask
bot-answerable, widen, replay or consume another agent's approval. (Research 4.)

## D-RA-2 The operator's text is rendered by ddflow, not by the agent

The prompt an operator answers shows the canonical command and the state digest, rendered by ddflow; the agent's reason is
quoted as untrusted. A relayed "the operator agreed" (families B and `AskUserQuestion` as is) never satisfies a gated
command at level L0. (Research 4.1, 6.)

## D-RA-3 Assurance levels and the channels shipped, in order

L1 terminal (`ddflow approvals approve`, from any terminal or ssh), L4 signed (SSH signature over the canonical payload,
verified offline against an operator key list; the agent may courier it), L2 harness (MCP elicitation, and an opt-in
transcript-attested `AskUserQuestion`/permission-prompt attestation after the probe), L3 one-time code and chat/PR
approval (optional). The tier names the minimum level per verb. The docs state the limit plainly: without a separate
uid, a signature or a remote store, a same-uid agent can forge L1-L3 by editing files; that is visible, not
impossible. (Research 5.)

## D-RA-4 Three tiers; the approval system's own configuration is signed-or-terminal

T1 standing policy (opt-in rules with caps, reported), T2 per-instance approval (default for all current operator
acts), T3 signed or terminal only (approval config, roles, signers, forbidden-commands, command reviewers, key
rotation). Layers tighten only. (Research 7, 8.)

## D-RA-5 Audit and revocation are events

`ask.requested/answered/consumed/withdrawn/expired` with level, channel, approver, digest; `approvals log`,
`gate list --overrides`, report line, doctor warnings; request window and use window expire on their own; revoke is a
person's act. (Research 9.)

## D-RA-6 Surfaces: no new MCP tool

MCP: fold into `ddflow_session` (actions `ask` (returns a handle at once; bounded optional `wait_s`), `asks`, `withdraw`; no answering action) plus one shared optional
param `approval` on the gated actions. CLI: `ddflow approvals ...`. Prompt: one line in each refusal and in the
driver. (Research 10.)

## Questions for the operator (RA-decide), with recommended defaults

| # | Question | Recommended default | Why |
|---|---|---|---|
| Q1 | Adopt the request/approve/consume protocol on the ask engine for every gated command (D-RA-1)? | Yes | It is the only way to satisfy "agent executes with permission" without trusting wording |
| Q2 | Keep relayed consent (`--yes --reason`, "the operator said so in chat") for `upgrade`, `plan confirm`, skew override, or require `--approval`? | Keep accepting both for one release, doctor warns on relayed use; then per-verb switch `require_approval` (default false) | Do not break unattended flows while the new path lands |
| Q3 | Which channels first? | CLI (L1) + signed (L4) as the MVP; elicitation (L2) after `RA-probe-harness`; chat/PR optional | The first two work today in cloud sessions and need no client support |
| Q4 | Does the operator have or want an SSH/FIDO2 key for approvals, and is `[approvals].signers` read from the commit allowed-signers file when unset? | Yes to both; ddflow never creates or stores the key (D-CM-7) | One key list for commits and approvals |
| Q5 | Accept the transcript-attested `AskUserQuestion` answer as L2? | Off by default; opt-in `[approvals].accept=["harness"]` per verb, T2 only | Weak outside local sessions; cannot reach T3 |
| Q6 | Minimum level per tier | T2: L1 (any person channel); T3: L4, with L1 accepted only to enroll the first signer (then doctor flags "T3 protected only by L1" until done) | Matches today for T2; a forgeable L1 must not be able to add its own signer or rotate keys once one exists |
| Q7 | Which acts are T2 beyond today's refusals: `complete --force`, `claim --force`, `cleanup --apply`, `version cut`, `promote`, `decision supersede`? | The first two (they already have a hook/flag guard); the others stay ungated | Avoid new friction |
| Q8 | Standing auto-approval rules to ship (off by default): extra review round up to 5 per item/gate when the tree changed after triage; skip of a gate whose reviewer was unavailable 3 times; accept measured baseline-red partial? | Ship the three as examples, all off; operator enables per rule | Matches "justified grants up to 4-5"; each is reported |
| Q9 | Windows: request lives how long; an approval is usable how long after the answer? | Request 24 h (existing `ask_ttl_s`); use window 1 h; uses 1 (counted bundles up to `[grants].max_count`) | Short use window limits stale approvals |
| Q10 | Notification of a pending request: only brief/status/inbox, or also push (one-time code over ntfy/Slack)? | brief/status/inbox now; push when AW-sinks lands, code only with an explicit sink | Push to a channel the agent can read weakens the code |
| Q11 | Is a `serve` host (agent cannot reach the store) in scope for strong remote approvals? | Not in this phase; documented as the upgrade path via P-remote.* | Largest build; signatures give most of the value |
| Q12 | Auto-request on refusal (`[approvals].auto_request`)? | Yes | Zero-friction; the request is cheap and expiring |

## Rejected alternatives

* **A "trusted agent" flag or `--i-am-the-operator`**: an agent sets it. (Same class as unsetting `CLAUDECODE`.)
* **Accept the model's statement that the operator agreed**: indistinguishable from prompt injection.
* **A new MCP tool or family `ddflow_approvals`**: forbidden by `D-mcp-tool-families`; folding costs about three
  actions.
* **A second approval engine for requests**: duplicates `services/ask` and `services/approval` (r-reuse-core).
* **ddflow-managed signing keys or TOTP seeds on the agent's host**: custody inside the adversary's reach (D-CM-7).
* **Weakening the refusals** (letting agents run `approve`/`grant add` with a reason): removes the control.

## Consequences

Every gated command gains one refusal message that names the route. The ask engine's `verdict` must stop trusting the
self-reported `human`/`coded` flags for levels above L1; this removes the flag gap, not the same-uid forgery of L2/L3 (only L4 does). Hot files (`config.py`, `surfaces/exemptions.py`,
`api/gates.py`) are touched by single tasks that hold their leases; `RA-mcp` waits for `MTD-family-spec`.
