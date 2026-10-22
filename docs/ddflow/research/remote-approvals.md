# Remote approvals: how an agent runs operator-gated commands with the operator's permission

Task `RA-research` (phase `P-remote-approvals`), 2026-10-22. Operator request, verbatim (recorded with
`ddflow session prompt`): "however, we want the operator to be able to manage everything in this project remotely and
as much automated as possible and safe, so such commands should be possible to be executed by the agent with
operator's permission". Proposals for the operator: `docs/ddflow/decisions/remote-approvals.md`. Nothing here is decided.

Marks: **CODE** = read in this repository today; **PROBED** = run on this machine today (research note `R9f143f7928`);
**UNVERIFIED** = from knowledge of an external product or protocol, not exercised here: check before building on it.

## 1. The problem in one paragraph

A handful of commands refuse every agent identity ("approving is a person's act ... Run it from your own terminal").
The operator works remotely, often from a cloud session with no local terminal, so they typed two gate skips by hand
after the agent was refused. The refusals exist for good reasons (an agent must never approve its own work or forge
the operator's consent); the missing piece is a way for the operator to say "yes, that one" from wherever they are,
in a form ddflow can check and an agent cannot manufacture.

## 2. Inventory: what is operator-gated, and how the caller is classified (CODE)

**Who is the caller.** `services/identity.agent_marker` returns a reason when the call carries `--agent`,
`DDFLOW_AGENT`, a harness marker (`HARNESS_MARKERS = ("CLAUDECODE",)`), or arrived over MCP (`via_mcp`, always an
agent). "" means a person at a terminal. `services/actors.resolve_actor` maps that to a role (`[agent].operator_role`
for a terminal, `default_role` otherwise; a credential or a named actor can prove another role) and
`[agent].roles` maps role to capabilities (`approve`, `approve_marked`, `waive`, `*`). The code says plainly what this
is: a shell edit by an agent that unsets its markers is "the gap `identity.agent_marker` documents". Every human
checkpoint makes forgery **visible** (OS user, host, `human: true`), not impossible.

Three families of gate exist today; they behave differently and the design must treat them differently.

| Family | Mechanism | Sites (CODE) |
|---|---|---|
| A. Identity-refused (exit 3) | `agent_marker` -> `AP.refusal` ("run it from your own terminal") | `ddflow approve <id> <gate>` (partial required gate; `services/gate_accept` via `approval.grant`); `ddflow grant add` (`services/grants.add`); `ddflow reviewers approve` (`services/reviewer_trust`; D-reviewer-trust-r2); `ddflow chore approve` (`api/chore`); `ddflow export ack`, `export disable --lock`, enable of a locked document (`services/export/select`); `gate skip` / hand-recorded outcome over a FAILED review gate (`api/gates._agent_skip_refusal`, D-failed-review-gate); writing a `[[reviewer]]` of kind `command` through any agent surface (`ddflow_configure`, `config --set`, `reviewers detect --write`); the ask engine's `answer` (`services/ask/engine`, `ANSWER_SOURCES`) |
| B. Relayed consent (agent supplies `--yes`/`confirm` plus `--reason`; the claim "the operator said so" is trusted and recorded) | free text | `upgrade --self --yes --reason`; `upgrade apply` `confirm=["self"]`; `plan confirm --yes --reason`; `review --force --reason` past `[review].max_rounds` (D-review-force-with-reason; listed by `gate list --refuted`); MCP `allow_older_version` reason (D-upgrade-skew-guard, event `skew.overridden`); `grant add` rounds are the recorded alternative to this one (DX-operator-grants-recorded) |
| C. Hidden or hook-blocked | flag omitted from MCP (`flag_exempt`/`OVERRIDE_EXEMPT`) or refused by the Bash hook | `claim --force` over another clone's live lease (CLI only, B90b43dd2d8); `complete --force` and every `--force` matched by `[hooks].forbidden_commands` (exempt: `ddflow review --force --reason`); `git stash`, `rm -rf .ddflow` and the rest of that list; `export --force`; `--allow-older-version` (CLI). Known defect: B-forbidden-force-blocks-operator-authorised-complete (no per-call approval route) |

Not gated at all today (an agent can run them; listed so the tiers in section 7 are complete): `decision add/supersede`
(warn-by-default, D-decision-management), `rule add/edit/pin`, `cleanup --apply`, `version cut`, `promote`, `config
--set` of anything but a command reviewer, `hooks install`, `companions add`. They are candidates for tier T2 only if the
operator wants them, not blockers.

**Existing building blocks that already do most of the work (CODE):**

* `services/approval.py`: approve-by-digest. `grant` (person only), `check` (digest must equal; only `human` rows count),
  `use` (single-use token, spent under the log lock).
* `services/ask/engine.py` (SE-ask-core, done, **no caller yet**): `request` (subject, digest or content, ttl
  `[agent].ask_ttl_s`, optional one-time code kept only as a hash, `bot_answerable`), `answer` (refuses unless the
  actor's role source is terminal/credential/named and holds `approve`; digest must match; code required off-terminal),
  `withdraw`, `sweep` (expiry audit), `pending` (the inbox), `verdict` (the consumer's one question). Events
  `ask.requested/answered/withdrawn/expired` are folded by `core/handlers/asks`. An agent can `request` and read, never answer.
* `services/grants.py`: a recorded, counted, expiring permission (`review_round`, `count` up to `[grants].max_count`,
  `[grants].ttl_h`, standing `when` predicates) whose use is derived from gate-run evidence.
* `[agent].roles`, `[agent].actors`, `agent.ask_code_length`; `P-remote.auth` (paired tokens, hashed, revocable),
  `P-remote.ask` (the transport half: `POST /v1/answers`, MCP elicitation delivery), `D-remote` (roles; a trusted bot
  may answer only asks the operator marked bot-answerable), `AW-inbox` and `AW-sinks` (P-awareness: what waits on the
  operator, and the push sinks).
* MCP posture: `grant`, `chore`, `approve` have **no tool** on purpose (`surfaces/exemptions.py`), so tools/list stays small.

**Finding 1 (PROBED, `R9f143f7928`).** The ask engine's guarantee is a call-site check. The fold trusts the log: an
`ask.answered` line appended by hand (`log.append(...)`, `human=True`) makes `verdict()` return approved, **even for an
ask minted with a one-time code**, because `coded` is self-reported and `verdict` never re-checks it. `answer()` itself
did refuse the agent actor. So today no engine, ask or approval, protects against an agent that appends to the event
shard; the same is true of `approval.granted`. Any design that wants more than "visible forgery" must add something the
agent cannot produce: a signature, a secret the agent cannot read, or a writer the agent cannot reach.

## 3. Threat model

Adversary classes, weakest to strongest:

1. **Accident / prompt injection**: an agent (or text it read) simply tries the command, or relays "the operator said
   yes" it invented. Needs: no honour-system consent, a check that is not satisfiable by wording.
2. **Over-eager agent in the operator's uid, using ddflow's own surfaces** (MCP tools, CLI) without editing files.
   Needs: the answer surface refuses it (exists), requests are bound to what was shown.
3. **Same-uid agent with a shell and file access** (this deployment): can unset `CLAUDECODE`/`DDFLOW_AGENT`, call
   `ddflow` as "a person", append events by hand (Finding 1), read config and local key files, call any connector it has
   (Slack, Gmail, Linear, the `gh` CLI as the operator). Software on the same machine under the same uid cannot
   stop this; only secrets or devices outside its reach can.
4. **Compromised host**: out of scope except to say what survives (a hardware-key signature verified offline).

The goal is therefore stated honestly in two grades: **no forgery by classes 1 and 2, by construction**, and **no
forgery by class 3 only where the approval carries something class 3 cannot make** (section 5, levels L3 and L4).
Everything else is "visible": every use names the channel, the level, the approver and the digest, so a spot-check
(`gate list --refuted`, `approvals log`) finds it.

## 4. The protocol (request, approve, consume)

One protocol for every gated command, built on the ask engine (no second engine, per D-shared-engines-r2 (1)).

**4.1 Request.** The agent (or the refusal site, automatically when `[approvals].auto_request` is true) files an ask:

* `subject = cmd:<verb>:<item>:<gate-or-target>` (for example `cmd:gate.skip:fix-X:critic`);
* the **canonical command**: verb, normalized arguments, item, gate, the agent id that will run it, the reason the agent
  gave, and a **state digest** of what the command acts on (for `gate skip` over a failed review: the failed outcome's
  digest and the unresolved findings' digests; for `grant add`: the existing grants; for `upgrade`: the target version
  and plan digest; for `approve`: the existing `gate_accept.digest`). `digest = sha256(canonical JSON)`;
* the **text shown to the operator is rendered by ddflow from the canonical record**, never written by the agent; the
  agent's reason is quoted inside it as untrusted text. An agent therefore cannot describe a different action than the one
  the digest binds;
* `requested_by` (the agent id; only that agent may consume), `expires_at` (a short request window), `uses` (default
  1; a counted bundle such as "2 more review rounds" is one request with `uses = 2`, capped by the same
  `[grants].max_count`), and the **minimum assurance level** the verb's tier demands.

**4.2 Approve or deny.** A person answers by one of the channels in section 5 with the digest they saw. The engine
already enforces human-only answering, digest match, expiry, one-time code. New: the answer carries `level` and `proof`
(signature, code, harness attestation) and `verdict` re-verifies the proof instead of trusting the self-reported
`human` flag. This closes the **self-reported-flag** gap (threat classes 1 and 2): a forgery now needs a proof-shaped
artifact. It does **not** make L2/L3 unforgeable by a same-uid shell (class 3), which can fake a hook payload or edit a
transcript; only L4 (a signature the agent cannot make) closes that, and the docs say so.

**4.3 Consume.** The gated site stops asking "is the caller an agent?" and asks one function:
`operator_gate(verb, params, caller, approval_id)`:

1. person at a terminal (no marker), as today: allowed, unchanged;
2. agent with an `approval_id`: recompute the canonical digest **now** from current state (so a widened argument or a
   changed outcome yields a different digest), find the approved ask for that exact digest, requested by this agent,
   unexpired, not revoked, uses remaining, level at least the tier's; spend one use under the log lock
   (`ask.consumed`, new event kind, registered in the event registry), run the command, and stamp the effect event
   (`gate.skipped`, `grant`, `approval.granted`...) with `human: true, proxy: <agent>, ask: <id>, level, channel,
   approved_by`. "human" keeps meaning "a person decided this exact thing";
3. otherwise refuse (exit 3) and **name the route**: "ask pending as A7K2; the operator approves with `ddflow
   approvals approve A7K2` from any terminal, or by signature"; with `auto_request` the ask is filed in the same call.

Properties by construction: single use (spent under lock, a replay is refused), scoped (the digest binds verb, args,
item, gate and state), expiring, requester-bound, widening-proof (any change is a new digest and needs a new ask),
self-approval-proof (the agent identity cannot answer), revocable (`approvals revoke` writes `ask.withdrawn`; consume
re-checks). Class B (relayed `--yes --reason`) gains an optional upgrade path: `--approval A` in place of the
trust-me flags. Class B verbs are T2 by classification, but their per-verb `require_approval` defaults to **false**:
both forms are accepted for one release (decision Q2), `doctor` warns on each relayed use, and the operator flips
`require_approval` per verb when ready.

**4.4 Class S vs class A.** State approvals (partial accept, reviewer trust, chore globs, export ack) are today read
later by a `check`. Under the protocol the person's answer to the ask **is** the approval and the agent need not run
anything: `check` consults the ask verdict when the subject is a `cmd:` approval. Action approvals (skip, grant, upgrade,
force) are consumed by the agent's re-run of the exact command with `--approval A`. Same ask, same events.

## 5. Channels: what each proves, what a same-uid agent can forge

Levels (assurance of the **answer**, recorded on it and compared with the verb's tier minimum):

* **L0 relayed**: the agent says the operator agreed. Never accepted. (This is class B today.)
* **L1 terminal**: a person's own shell, no agent marker (`ddflow approvals approve`). Visible forgery only.
* **L2 harness-attested**: the client UI mediated the answer (MCP elicitation; Claude Code permission prompt), or a
  harness hook recorded it. Strength depends on the client; verified by ddflow only as far as the client's integrity.
* **L3 secret-or-identity-attested**: a one-time code delivered out of band, or a forge-authenticated identity
  (PR review by a named login). Forgeable by an agent that can read the delivery channel or holds that identity's token.
* **L4 signed**: a signature by an operator key whose private half is not on the agent's machine (or needs a touch),
  verified offline against `[approvals].signers`. The only level class 3 cannot forge on its own.

| Channel | Level | Remote? | What a same-uid agent can forge | Needed to be strong | Notes |
|---|---|---|---|---|---|
| `ddflow approvals approve` in any terminal (local, ssh, Mosh, a phone terminal) | L1 | yes, if the operator has an ssh path | Unset markers and run it itself; append the event (Finding 1) | Separate uid or L4 | Works today with no new crypto; the operator's current workaround, made one command |
| Pending list in `ddflow brief/status`, `approvals list` | n/a | n/a | Nothing (read) | n/a | Where the operator sees what waits (AW-inbox registers as a source) |
| MCP **elicitation** (`elicitation/create` issued by the ddflow server during the agent's `approvals ask` call) | L2 | yes (any client UI, including a cloud session's app) | The model does not see or author the response; a settings-level auto-accept hook could answer, and the agent can edit settings if it can write them | Client integrity; deny writes to harness settings | **UNVERIFIED** that Claude Code, its cloud/bridge sessions and each other client render elicitation and return the person's response without the model; `RA-probe-harness` decides |
| `AskUserQuestion` / chat turn in the agent's own session | L0 as is; L2 only with transcript attestation | yes (it is the channel this session uses) | The tool result reaches ddflow only through the model: it can claim any answer. With a PostToolUse hook that records the answer, or a check of `transcript_path` (question text equals ddflow's rendering, `tool_use_id`, answer), forgery needs a shell call faking the hook or editing a transcript file | Hook/transcript outside the agent's write reach; **UNVERIFIED** in cloud sessions | Honest answer to "can an agent-mediated question be bound": only if the question text is ddflow's rendering and ddflow reads the answer from a record the harness wrote, not from the model; otherwise not secure |
| Harness permission prompt (a PreToolUse hook returns `ask` for `ddflow approvals use ...`) | L2 | yes | Anything the user pre-allowed (`ddflow *`) bypasses the prompt unless the hook's `ask` overrides allow rules; `bypassPermissions` mode removes it. The hook payload carries `permission_mode`, so ddflow can refuse in bypass modes | Hook that cannot be turned off by the agent (managed settings) | **UNVERIFIED**: precedence of hook `ask` over allow rules and modes; probe it |
| One-time code sent to the operator out of band (ntfy/Slack DM/email by a ddflow sink), typed back as `approve A7K2 <code>` | L3 | yes | Read the same channel with its own connector (this session has Slack, Gmail, Linear connectors acting as the operator) or the sink's webhook secret | Delivery channel the agent cannot read; code never stored (engine keeps only the hash) | Raises class 1 and 2 only; good for T1/T2 |
| Chat/ticket approval (Slack reaction, Linear comment, GitHub PR review `APPROVED` or `/approve A7K2` by a named login, via `infra/forge.py`) | L3 | yes | Post as the operator if the agent holds that connector or `gh` token. GitHub also refuses an author approving their own PR, a useful independent control | Agent uses a distinct bot identity/App; allow-list of approver logins; reviewer != author check | Optional channel (RA-channel-chat) |
| TOTP | L3 at best | yes | Read the secret from config on the same uid | Verifier on another host holding the secret | Not recommended local-only |
| Local unix socket / FIFO served by a ddflow daemon, `SO_PEERCRED` | L1 -> strong | no (local) | Same uid: connect as itself | Separate uid owning the socket, agent sandboxed away | Needs the opt-in `serve` (D-remote); documents the real boundary |
| `ddflow serve` on a host the agent cannot reach, operator token, `POST /v1/answers` | L2-L4 | yes | Nothing, if the agent host holds only a request-only token | The store (log writer) is on the other host (D-storage staged remote queue) | The architecture that makes remote approvals strong; the largest build |
| **Signed approval**: `ddflow approvals show A7K2 --sign-payload` prints the canonical text and digest; the operator signs it where their key lives (`ssh-keygen -Y sign -n ddflow-approval`, GPG, or a FIDO2 `sk-ssh-ed25519` key needing touch) and hands back the signature (paste in any channel, even through the agent: it cannot alter it) | L4 | yes (phone terminal, any machine with the key) | Cannot sign. Can only replay a signature for the same digest, which is single-use and expiring | Public keys in `[approvals].signers` (project) with company floors; private key off the agent host | Verifiable offline with `ssh-keygen -Y verify` (**UNVERIFIED** here: tool present? probe in RA-channel-signed); shares the signer list and "never holds keys" rule with CM-sign (D-CM-7, D-CM-Q4Q5) |

**Is the signed relay safe even though the agent carries it?** Yes: the signature is over the canonical digest
(namespace `ddflow-approval`, includes ask id, digest, outcome, expiry). The agent can pass it along, withhold it, or
replay it once; it cannot edit it or sign anything else. This is the one channel where the operator can approve
remotely from a phone while the agent is the courier, with no assumptions about the client or the host.

**Honest bottom line.** Without a separate uid, a daemon on another host, or a signature, a same-uid agent can always
obtain approvals by tampering with files. What the lower levels buy is real but bounded: the operator's yes is
recorded, bound to an exact action, single-use and visible; accidents and injections cannot satisfy it. What
ddflow can verify: signatures, digests, expiry, uses, requester binding, code hashes. What it cannot verify: that a
terminal had a human behind it, that a client UI was not auto-accepted, that a connector account is not shared with the
agent.

## 6. Where an agent-mediated question cannot be trusted (the AskUserQuestion case)

`AskUserQuestion` answers come from the human, but they reach ddflow only as part of the model's context. ddflow is not
in the path, so a bare "the operator chose Yes" in an argument is exactly class B. It can be tightened in three ways,
none fully secure: (a) ddflow renders the question and the harness hook records the tool result itself
(`PostToolUse` matcher `AskUserQuestion`, written by the hook process, not the model); (b) ddflow verifies the answer in
the transcript file named in the hook payload (**UNVERIFIED** in cloud sessions where the file may not be reachable);
(c) the question and the signed route are combined: the human is asked, and the actual approval is a signature or a
code. Recommendation: treat (a)+(b) as an **opt-in L2** (`[approvals].accept = ["harness"]`) labelled "attested by
transcript", never as default, and prefer elicitation and signatures.

## 7. Risk tiers

* **T1: pre-authorised by standing policy**, no human in the loop per instance, but reported. Examples (operator opts in
  per rule, with caps): an extra review round up to N per item/gate (the existing "justified grants up to 4-5"); `gate
  skip` of a gate whose reviewer was recorded `unavailable` after K retries (the rule "unavailable, never passed" holds:
  the skip is a recorded skip); accept a `partial` that ddflow **measured** as baseline-red (D-accept-baseline-red-partial);
  `claim` takeover of a lease expired for more than a day. Auto-approvals are `ask.answered` with `by=policy:<rule>`,
  `human=false`, `policy=true`, and the consumer counts them **only** for verbs that list policy as acceptable.
* **T2: per-instance approval**. Family A verbs are T2 and enforced. Family B verbs are classified T2 too, but their per-verb
  `require_approval` (section 4.3) defaults to **false**, so until the operator switches it on both the relayed flags and
  `--approval` are accepted. The T2 verbs: `gate skip` over a failed
  review gate, `grant add`, partial accept, `reviewers approve` (HTTP entries), `chore approve`, `export ack`, `claim
  --force`, `complete --force`, `plan confirm`, `upgrade --self`, skew override, `version cut`, `promote`, `cleanup
  --apply`. Minimum level L1 (terminal) by default, L2/L3/L4 accepted; the operator can raise per verb.
* **T3: signed only** (no lower level is ever accepted): the approval system's own configuration (`[approvals]`,
  `[agent].roles/actors`, `[agent].operator_role/default_role`, the signer list, key rotation), `[hooks].forbidden_commands`,
  command-kind `[[reviewer]]` entries (they execute code; D-reviewer-trust-r2), disabling hooks, company-layer floors.
  **Root of trust, stated honestly:** ddflow cannot approve its own first signer: any answer it could accept before a signer exists is
  an L1 answer a same-uid agent can forge, and a signer list in an agent-writable file is not a root. The root is **one out-of-band act by
  the operator**, like pinning an ssh `known_hosts` entry: a digest of the signer list (or the keys themselves) written to a layer the agent
  surfaces cannot write (the user layer outside the repo, or a company pack pinned by digest). `[approvals].signers` and the
  commit allowed-signers fallback are honoured **only when they match that pin**; a signer file edited later does not match and is
  ignored with a `doctor` finding, and rotation is a T3 act signed by a key already in the pinned list. Until the pin exists T3 verbs are
  not approvable by `approvals approve`, `doctor` reports "no approval signer pinned: T3 protected only by the shell-edit gap documented
  in `identity.agent_marker`", and the residual risk equals today's. A same-uid agent that can also write the user layer defeats the pin;
  that needs a separate uid or machine, stated in the docs. An agent may **file** the request (so the operator sees exactly what
  to sign) but no lower-level answer is accepted.
* **T0: ungated** stays ungated.

Layering: the tier table is data (`[approvals.verbs]`), layered as everything else (D-config-layer-order, D-org-config):
a company or user layer may only **tighten** (raise a tier, raise a minimum level, shorten a ttl, remove a policy rule);
project and local layers cannot loosen what a higher layer set. An agent surface cannot write `[approvals]` at all (T3).

## 8. Auto-approval policy (automation the operator opts into)

`[[approvals.policy]]` rules, each: `id`, `verb`, `when` (a named predicate over facts ddflow computes, the existing
`grants.Ctx` pattern: tree changed after triage, reviewer unavailable K times, baseline-red measured), `max_uses`
(per item and per day), `ttl`, `level_label = "policy"`. Defaults ship **off**; `ddflow doctor` suggests rules from
the log ("you approved 9 identical `review_round` requests; add a rule?"). Every use is reported in the end-of-run
report and in `approvals log`; a policy that fires more than its cap refuses and falls back to T2. Predicates are
registered in code (like `GrantKind.whens`) so a rule can never name an arbitrary command.

## 9. Auditability

Events: `ask.requested` (digest, rendered text, requester, tier), `ask.answered` (level, channel, proof digest,
approver), `ask.consumed` (agent, effect event id), `ask.withdrawn` (revocation), `ask.expired` (sweep). Reports:
`ddflow approvals log [--since --verb --agent]`; `gate list --refuted` gains `--overrides` (every gate settled by a skip,
an accepted partial or a forced round, with its approval id and level); `ddflow report` lists approvals used this run;
`doctor` warns about approvals expiring unused, consumed by a different level than the tier asked, hand-written
`ask.answered` lines (no proof for an L2+ verb) and signer keys near rotation. Auto-expiry: `[approvals].ttl_s` for the
request window, a shorter `use_ttl_s` after approval; the sweep writes `ask.expired`. Revocation: `approvals revoke`.

## 10. Surfaces (D-mcp-tool-diet: no new tool)

Families after the diet (15, from the matrix in `research/mcp-tool-diet.md`): `view task reviewers memory doctor admin gate
workflow rule ship lease decision session job bug`. Approvals is not a noun in that list and **no 16th tool is allowed**. Recommended home:
`ddflow_session` (it already carries `session prompt`, the operator's recorded words, and `note`): **new actions `ask`
(file a request and return a handle **at once**, never blocking: the agent takes the next item, per r-lock-cycle-selfheal; whether
an elicitation prompt can be issued without an open tool call is what `RA-probe-harness` decides; **if it cannot, the elicitation channel is
not offered for that client** (the permission-prompt and signed channels remain) rather than adding any blocking wait), `asks` (list/show the agent's own
pending and answered requests) and `withdraw`**. Consuming needs no action: the gated actions gain one optional,
deduplicated union parameter `approval` (the diet already shares `as_agent` once). Answering (`approve`, `deny`,
`revoke`) has **no MCP action**, like `approve`, `grant` and `chore` today (exemptions): the MCP surface can ask and
read, never answer. Schema delta (to be measured by LS-measure/MTD-budget-test): about three actions plus one param,
roughly 0.9 KB of `ddflow_session` schema (estimate, **UNVERIFIED**). Second choice: `ddflow_gate` (the most frequent
requester), rejected as the home because requests are not all gate acts. CLI: top-level `ddflow approvals
request|list|show|approve|deny|revoke|withdraw|log|sign-payload`; the family mapping is the declared matrix's
`family`/`action` fields (MTD-family-spec), so the CLI noun need not equal the family. Prompt: the refusal text and the
driver name the route in one line (RA-prompt).

## 11. Interplay with other work

* `P-remote.ask` is the transport half of the ask engine (`POST /v1/answers`, elicitation delivery). This phase builds
  the **consumers** (nothing calls the engine today) and the proof levels; the transports plug into `level`/`proof`.
* `P-agent-lifecycle` (new-agent consent, redo, offboarding) and `CM-packs-update` ("ask the operator, then download")
  are consumers: they file asks with `cmd:` subjects instead of inventing flows.
* `P-commit-messages` / `CM-sign`: ddflow "never generates, stores or copies a key" (D-CM-7). Approval signing follows
  the same rule and should read one signer list: `[approvals].signers` falls back to the commit allowed-signers file.
* `P-tool-diet`: RA-mcp needs the declared `family`/`action` fields (`MTD-family-spec`, running).
* `P-awareness`: `AW-inbox` lists pending approvals; `AW-sinks` carries the notification (and the one-time code).
* `P-lean-start`: a pending-approvals line in the brief must fit the lean charter; RA-brief waits for `LS-lean-profile`.

## 12. Gaps found while reading (filed separately)

The refusal text names no route for a remote operator; `ddflow approve` is undocumented in `--help` beyond usage; the
ask engine has no caller; its `verdict` ignores the code; family B trusts free text; the forbidden-force hook has no
per-call approval (B-forbidden-force-blocks-operator-authorised-complete); the CLI hides `grant`/`approve`/`chore`
from MCP with no route to ask.
