# Commit message management: decision proposals

Status: **DECIDED 2026-10-22** (task `CM-decide`; operator answers recorded as decisions `D-CM-Q*`). Where an answer
amends a proposal below it is marked AMENDED and the answer wins; D-CM-1..12 not marked are ACCEPTED as proposed. Evidence: `docs/ddflow/research/commit-messages.md` (section numbers below
refer to it). Phase: `P-commit-messages`. Governing records: `r-everything-configurable`,
`r-everything-managed`, `r-company-workflows`, `D-config-layer-order`, `D-org-config`,
`D-mcp-tool-diet`, `D-strip-coauthor-history-r2`, `D-fallback-no-remote`.

## D-CM-1 Three separate things: context, template, policy

Commit message management has contexts (which situation), templates (how text is produced) and a policy
(what any message must satisfy). The policy is the contract and a deterministic lint enforces it; a
template is one producer and must pass it. Alternatives rejected: one big template per project (cannot
express a company floor); policy as prose only (not checkable, and a model would judge it).

## D-CM-2 Layering reuses the config layer order; policy is tighten-only

Layers are exactly `D-config-layer-order`: default < company defaults < user < project < machine-local <
env < CLI < session < company ENFORCED. No second layering mechanism. Policy fields merge by tightening
(section 5.3: unions, minimum of maxima, conjunction of regexes, maximum of sign mode); a loosening value
is ignored and reported unless a scoped, expiring waiver exists. A template from a lower layer may replace
a higher layer's template except one marked `enforced`, but its output must pass the merged policy.
Rejected: "last writer wins" (a project could drop a company rule); user above project (note the operator's
wording lists user last; see question Q2).

## D-CM-3 Storage: two data record kinds plus a `[commit]` knob section

`commit_template` and `commit_policy` are data kinds (`ddflow/templates/kinds/*.toml`) managed by the
generic record engine (add, edit, remove as tombstone, revise, history, search); scalar behaviour lives in
`[commit]` knobs, every one settable by file, CLI, MCP and prompt through `configwrite` and logged.
Shipped presets are copies, never edited in place. Existing `[enforce]` trailer knobs stay as aliases of the
default project policy for at least one release. Rejected: a new `.ddflow/commit.toml` subsystem with its
own loader (duplicates the overlay/record machinery).

## D-CM-4 Renderer: sandboxed Jinja, curated read-only context, caps

Templates render in a `SandboxedEnvironment` with `StrictUndefined`, a time cap and an output cap, from a
curated context of plain values (section 5.4); no environment, paths, secrets or callables. One shared
environment factory replaces the three sandbox setups that exist or are planned (task CM-sandbox).
Templates are data (instruction text), not executable extensions, and honour `[config].template_trust`.
The lint never imports jinja (hook path cost). Rejected: `str.format` (the one place that uses it,
`commit_events_message`, cannot express a loop or a conditional); unsandboxed Jinja.

## D-CM-5 The model supplies fields; templates render; lint decides

For ddflow-owned commits the agent passes structured fields, never the final string. A lint refusal
returns machine-readable violations and allows at most `[commit].draft_retries` (default 2) redrafts. No
model runs in a hook and no model judges conformance; a style review is an advisory reviewer lens.
Rejected: letting a model rewrite the message after lint (the contract would be unverifiable).

## D-CM-6 One lint engine, four doors

A pure-stdlib engine is called by (1) the `commit-msg` hook (`ddflow hooks check-msg`, extended), (2) ddflow
itself on every message it generates, in process, so `--no-verify` and plumbing cannot skip it, (3) a new
gate `commits` over base..head, (4) the pre-commit-framework hook (stage `commit-msg`, the existing
`precommit` proposal). `prepare-commit-msg` pre-fills skeleton and trailers (it is not skipped by
`--no-verify`). Default policy preset `plain` keeps today's behaviour: Co-Authored-By forbidden on this
project (configured, as now), `Item:` trailer required only where `require_item_trailer` is on.

## D-CM-7 Signing: ddflow orchestrates and verifies, never holds keys

`[commit].sign = off | auto | required` (default `off`), `sign_scope = merge | tips | all` (default
`merge`), `sign_verify = git | gitsign`. ddflow adds `-S` to its own plumbing commits (they ignore
`commit.gpgsign`), verifies with `git verify-commit` against the configured allowed-signers, and treats an
inability to sign or verify as `unavailable` (exit 2), never a pass or a silent unsigned commit under
`required`. It never generates, stores or copies a key. `sign` is a policy-tier knob (agent writes become
pending proposals). Rejected: ddflow-managed keys (custody and blast radius); signing only by config
(plumbing commits would be silently unsigned).

## D-CM-8 Sign-off is a person's act

`Signed-off-by` (DCO) certifies origin, so `[commit].signoff = never | person | policy`, default `never`.
`person` adds it only to commits made under a person identity through the CLI (like `chore approve`);
`policy` (a company with a DCO) requires the trailer to be present and lets the human add it; an agent
identity never fabricates one. Rejected: auto sign-off by every agent for the operator's name.

## D-CM-9 Push: never by default, one guard, server-side stays the real wall

`[commit].push = never | branch | allowed` (default `never`), `push_protected`, `push_require_signed`,
`push_require_lint`; the knob is `outward` and `policy`-tier, so a typo is the safe value. One guard
function is called by every ddflow push site; an optional generated `pre-push` hook applies it to pushes
ddflow does not make (advisory). Existing operator-set pushes (`flow.integration=pr`, `flow.claims=remote`)
stay allowed and the guard names the setting that allows them. Force push is always refused.

## D-CM-10 Surfaces: no new MCP tool

CRUD of templates and policies: the existing `ddflow_record` (family `doctor`), kinds `commit_template` and
`commit_policy`: no schema delta. Config: the existing `ddflow_configure`: no delta. Evaluators (`render`,
`lint`, `test`, `status`): four new `action` values and five optional params on the existing
`ddflow_doctor` family. CLI: `ddflow commit render|lint|test|status` plus a CLI-only alias set
`ddflow commit list|show|add|edit|remove` onto `ddflow record commit_template`. By prompt: a workflow
command prompt `commit-policy`, every change an event, reviewable with `ddflow record ... history`.
Alternatives: (B) the `rule` family with a `kind` option (smallest family, but rules are guidance text and
the record tool already does CRUD); (C) the `admin` family (largest union already, up to 42 params); (D) a
new `ddflow_commit` tool (rejected by the operator's constraint). Depends on MTD-family-spec,
MTD-family-schema and MTD-profiles.

## D-CM-11 Packs, extension and presets

Presets (`plain`, `conventional`, `angular`, `kernel`, `jira`) ship as copies. Users add contexts
(`[[commit.context]]`) and static variables (`[commit.vars]`). Company packs travel in the org bundle
(`commit/`), are pinned by digest in the org lock, are data only, and appear in brief/doctor with the
source named; a local file or git source is allowed, a bare URL without a digest is not. Executable
content in a pack (none is defined) would stay inert until approved, as for every org kind.

## D-CM-12 Migration

The 14 generation sites (research section 6) move to templates one group at a time with the old text as the
shipped default, so history looks identical until a layer changes it; a ratchet test (CM-inventory) fails
when a new `commit -m`, `merge -m` or `commit-tree -m` appears outside the registered contexts. The
`agent_brief.md` guidance sentence is rendered from the effective policy. Nothing rewrites existing history.

## Operator answers (2026-10-22)

| # | Answer | Status | Decision record |
|---|---|---|---|
| Q1 | `doctor` family | ACCEPTED (D-CM-10 A) | D-CM-Q1 |
| Q2 | Follow `D-config-layer-order` (user below project); user may only tighten | ACCEPTED (D-CM-2) | D-CM-Q2 |
| Q3 | "Plain + Item trailer": plain subject, `Item: <id>` trailer by default | AMENDED (D-CM-6 preset) | D-CM-Q3 |
| Q4 | Signing default `auto` (sign if a key is configured, else skip and report) | AMENDED (D-CM-7 was `off`) | D-CM-Q4Q5 |
| Q5 | `sign_scope` = `merge` | ACCEPTED | D-CM-Q4Q5 |
| Q6 | Sign-off added automatically from the configured git identity; tighten-only knob; no identity => refuse (exit 2), never a placeholder; never Co-Authored-By | AMENDED (D-CM-8 was `never`); tension with DCO attestation flagged | D-CM-Q6 |
| Q7 | Push allowed on request by default, subject to company policy, protected branches (`main`, `master`, `release/*`) and project config; never unasked | AMENDED (D-CM-9 was `never`) | D-CM-Q7 |
| Q8 | Redraft bound 3 | AMENDED (D-CM-5 was 2) | D-CM-Q8Q13 |
| Q9 | Packs pinned by digest by default; init at onboard/upgrade, verify on use, periodic watch, update only with permission after a diff | AMENDED (D-CM-11); tasks CM-packs-init/-verify/-watch/-update/-diff | D-CM-Q9 |
| Q10 | `require_item_trailer` on for this project | ACCEPTED | D-CM-Q10Q12 |
| Q11 | Audit one release, then enforce | ACCEPTED | D-CM-Q10Q12 |
| Q12 | `[enforce]` trailer knobs stay as aliases for two releases, then removed | ACCEPTED (D-CM-3) | D-CM-Q10Q12 |
| Q13 | Plain subject limit 72 (50 opt-in) | ACCEPTED | D-CM-Q8Q13 |

Q9 verbatim: "with pinned digest by default, unless the user or the config option changes this on per project basis. We also
want the user to have the option to initialize it (and pin it with digest) when the project is bootstrapped or upgraded,
and then once pinned to check the digest. If a change happens (periodical checks should be made for every corporate
policy) and there is a pinned digest, the user should be asked and with permission the new policies could be downloaded.
Also the user might have to receive a comparison - what the new policies are changing compared to the old". Approval is a
person's act (agents cannot approve; reuse `ddflow approve`/grants); fetched content is data, never instructions.

Q7 verbatim: "Allow push on request by default, as long as it doesn't conflict company policy or other project dependent rule or configuration".

Q6 caveat: Signed-off-by is a person's attestation (DCO). The append is a knob, tighten-only per layer, so a company or
project can disable or require it; it uses the configured git identity only. Co-Authored-By is never added.

## Original open questions and recommended defaults (superseded by the answers above)

| # | Question | Recommended default |
|---|---|---|
| Q1 | Which MCP family carries the four evaluator actions? | `doctor` (D-CM-10 option A) |
| Q2 | Layer order: follow `D-config-layer-order` (user below project) or put user above project for personal style? | Follow it; a user may only tighten, personal preferences live in `[commit]` style hints that never touch policy |
| Q3 | Shipped default style: keep ddflow's own (`plain`) or Conventional Commits? | `plain`; offer `conventional` as a preset; do not change this project's history style |
| Q4 | Signing default | `off`, `doctor` suggests `auto` when git is already set up; `required` only by explicit choice |
| Q5 | `sign_scope` when `required` | `merge` (portable across squash/rebase) |
| Q6 | Sign-off | `never`; `person` for a DCO project; agents never add it |
| Q7 | Push default and protected branches | `never`; protected `main`, `master`, `release/*` |
| Q8 | Redraft bound after a lint refusal | 2, then fail with the violations |
| Q9 | Org packs from a URL | Allowed only with a pinned digest; otherwise local file or git source |
| Q10 | Turn `require_item_trailer` on for this project | Yes, with `Phase-ships` waivers as today; keep off for others |
| Q11 | Enforcement for a new policy | `audit` for one release (report only), then `enforce` |
| Q12 | Keep `[enforce]` trailer knobs | Yes as aliases for two releases, then remove with an upgrade-manifest note |
| Q13 | Subject limit in the `plain` preset | 72 (50 opt-in) |
