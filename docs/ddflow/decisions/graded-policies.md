# Graded corporate policies: strength levels and "one of" alternatives

**State: DECIDED (operator, 2026-10-23).** The operator answered the section 13 questions (table at the end of section 13); each answer is a decision record `D-GP-Q1` .. `D-GP-Q11`. Where an answer differs from the recommendation (Q4, Q9) the answer governs and the text above is superseded by it. Evidence: [research/graded-policies.md](../research/graded-policies.md).

Operator request, 2026-10-23, verbatim: "For the centralized corporate policies investigate the option to have soft and strict policies like rules too, like this is recommendation, this is nett practices, this is required, and each of them could be multiple options like it is required to do A or B or C..."

## 1. Summary

Add two things to the record ddflow already has for rules, decisions and (planned) requirements (`GuidanceRecord`), not a new kind and not a new tool:

1. a **strength** with five spoken levels, defined as a fixed view over the existing `enforcement x override` pair;
2. a **satisfied_by** expression: `all`, `any`, or `at_least k` over a set of named **options**, each option an existing check (a gate outcome, a config value, a tool present, an evidence kind, a command, a pattern, a file).

Company policy is the same record arriving from the org bundle in the top layer, pinned by digest; a project may raise strength and narrow options, never lower or widen. Everything evaluates three-valued (pass, fail, unavailable) and `unavailable` is never a pass.

## 2. The strength scale

Spoken words (RFC 2119 flavour, upper case only in prompts), what they mean, and the existing fields they are a view of:

| Strength | Meaning | RFC 2119 | enforcement | override | Who may set aside, how |
|---|---|---|---|---|---|
| `info` | Context. No action implied. | MAY | advisory | n/a | nobody needs to |
| `recommended` | Good idea. Declining needs nothing. | SHOULD (weak) | advisory | n/a | silently |
| `best-practice` | Default for everyone. Follow it or say why not (comply or explain). | SHOULD | warn | deviate | the agent or user, reason required, scope item/project/machine, no approver, visible |
| `required` | Must be satisfied; the check fails the gate. | MUST | block | waive | a person approves a scoped, reasoned waiver, at most 90 days (`waivers.py`) |
| `forbidden` | Must not happen; polarity of `required` (MUST NOT). | MUST NOT | block | waive | as `required` |

Two refinements, both orthogonal to the five words:

- **locked** (company ENFORCED layer only): override = `none`. Nobody below can waive or deviate; only the company layer can lift it (its own expiring exception). This is `D-goals-principles` "org floor = block + none". `locked` is a property of the layer a record arrives in, not a sixth strength. For alternatives the separate marker is `closed` (D-GP-Q4): no lower layer may add an alternative to this requirement; `closed` and `locked` are independent axes and a company may set either or both.
- **mode** `audit | enforce` per record (Kubernetes PSA and Kyverno style): `audit` evaluates and reports at the strength's visibility but never blocks. This is the already-decided "a new policy audits one release, then enforces" (`D-CM-Q11`, `D-org-config` audit mode) generalised: a `required` policy can ship in `audit` first. Default for a company-pushed record: `audit` for the first release after it appears in the lock, then `enforce`; a project record defaults to `enforce`.

Severity (impact if violated) is NOT strength. A `severity` label may rank and colour output; it never obliges (same rejection as `priority` in `D-requirements-model`). Out of scope for the first cut.

Why five and not three: `MUST/SHOULD/MAY` is already decided for requirements (`D-requirements-model`) and stays the machine mapping. The operator asked for a visible split between "recommendation" and "best practice"; that split is real: both are SHOULD, but only best practice demands an answer before `complete`. `forbidden` is spelled out because agents handle "do not" better as its own word, and because lint can check polarity.

## 3. Alternatives: `satisfied_by`

A policy record gains:

```toml
[policy.options]                      # named options; each is a check table of an existing kind
gpg    = { kind = "command", argv = ["git", "verify-commit", "HEAD"], expect_exit = 0, signer = "gpg" }
ssh    = { kind = "config",  key = "commit.sign_verify", in = ["git"], and_config = "gpg.format=ssh" }
gitsign = { kind = "tool",   companion = "gitsign" }
human  = { kind = "gate",    gate = "critic", outcome = "passed", reviewer = "person" }
model  = { kind = "gate",    gate = "critic", outcome = "passed", family = "other" }

satisfied_by = { any = ["gpg", "ssh", "gitsign"] }
# also: { all = [...] }  { at_least = 2, of = [...] }  and nesting:
# satisfied_by = { all = [ { any = ["gpg","ssh","gitsign"] }, "signed-off" ] }
```

- Default (no `satisfied_by`) is `all` of the record's `checks` — exactly today's behaviour, so nothing changes for existing records.
- Options are the existing `checks` tables, given ids (`check_id` already supports `id`). New kinds are thin bridges, registered once in the checks registry: `gate` (outcome of a pipeline gate on the item), `config` (a knob equals/in a set), `tool` (a companion or binary present, via `ddflow companions`), `evidence` (an evidence kind recorded for the item), `approval` (an approval of a given subject exists). The existing planned kinds `command`, `pattern`, `file`, `reviewer`, `mcp` (RQM-check) are reused, not duplicated.
- Depth is capped (2 levels) and option count capped (knobs), to keep the expression a list, not a language.

### 3.1 Three-valued evaluation (Kleene logic)

Each option yields `pass | fail | unavailable` (plus `waived`, below). Combination:

| Expression | pass when | fail when | otherwise |
|---|---|---|---|
| `all` | every operand passes | any operand fails | unavailable |
| `any` | any operand passes | every operand fails | unavailable |
| `at_least k of n` | passes >= k | passes + unavailable < k | unavailable |

Consequences, each a test: an `unavailable` option never satisfies an `any`; an `any` whose options are all `unavailable` is `unavailable` (not fail, so the gate records `unavailable`, `gates.unavailable_is_failure` decides blocking, the same as today for a check that could not run); an `all` with one real `fail` fails even if another operand is unavailable (a known failure is not masked by a coverage gap).

### 3.2 What the result says

The result carries: `satisfied_by` = the option id that passed first in evaluation order (or ids, for `all`/`at_least`), `tried` = every option evaluated with its status and reason, and for a non-pass the reason none did ("gpg: unavailable (gpg not installed); ssh: fail (allowed_signers unset); gitsign: unavailable (companion absent)"). That text is what an agent reads. The result digest (existing `CheckResult.digest`) includes the chosen option, so a cache keyed by tree fingerprint stays correct.

### 3.3 Order, cost, caching

Operands are evaluated cheapest first (declared `cost`: config < file < tool < gate < pattern < command < reviewer), short-circuiting: an `any` stops at the first pass; a `reviewer` option (a model call) only runs if cheaper options did not decide it. Results cache by (record digest, option id, tree fingerprint), reusing the existing result digest. A `gate` option reads the recorded outcome and never reruns the gate.

### 3.4 Waiving an alternative

A waiver covers a record and, optionally, a single option or the whole expression (the existing `check` field). Waiving one option is meaningless for an `any`; the useful waiver is of the policy for given globs. The Azure-style split is kept simple: a waiver says "accepted risk"; "satisfied another way" is just another option listed in the policy (or an exception option the company adds) — so it is auditable as a pass, not a waiver.

## 4. Layering and escalation

Layers are `D-config-layer-order`; policy is evaluated per layer and merged tighten-only with ONE combiner (replacing the five separate ones; see GP-model):

| Field | Lower layer may | May not |
|---|---|---|
| strength | raise (recommended -> best-practice -> required) | lower below the company value |
| polarity | keep | flip `forbidden` to allowed |
| options | remove options (narrow); add a new option only inside an `all` conjunct | (Q4, DECIDED: a project MAY add an alternative to a company `any`/`at_least` by default, as an evaluable named check, reported in the digest; a company `closed` marker forbids it, and a `locked` requirement is not changed by a lower layer. The older text here said no; superseded by D-GP-Q4.) |
| `at_least k` | raise k | lower k |
| `any` -> `all` | yes (tighten) | `all` -> `any` |
| mode | `audit` -> `enforce` | `enforce` -> `audit` |
| scope (globs) | widen the scope | narrow below the company scope |
| override | `waive` -> `none` (lock) | `none` -> anything |

A project record that "loosens" is refused on every write surface (exit 3, naming the policy item, layer and revision, like `B-org-policy`), and at load for a hand edit it is ignored and reported by doctor (company policy wins, not fail-open). The only way to lower is a waiver or a company-issued exception. Escalation: a company may raise a best practice to `required` in a later release; the lock-bump update flow (`D-org-config`) shows that as a diff (section 8) and the record starts in `audit`.

Opt-out of `best-practice` (a deviation): item scope by default; project/machine scope allowed with `review_by` and a reason; a company can set `opt_out = reason | approval | none` on a best practice (`approval` makes it a required-with-person-override, `none` is effectively required). Repeated deviations of one record are counted and shown (the `RQM-strength` "deviation counter"): a high deviation rate is the signal that the practice is wrong or badly worded.

## 5. Waivers and approvals

- ONE waiver record (`SE-waiver`) used by `required`/`forbidden`: reason, glob scope, expiry (<= 90 days, `waivers.MAX_DAYS`, a knob), approver through the approvals engine. Approval tier follows `D-RA-4`: T2 (any person channel L1-L3) by default; a waiver of a **locked** company policy is not available to a project; a company-layer exception to a locked policy is T3 (L4 signed, once `RA-channel-signed` exists; until then terminal and rated L1 on a single-uid host, which doctor already says).
- A deviation (best-practice) is the same record in mode `deviation` (`B-goal-deviation`): no approver, no expiry (dies with the item) at item scope; project/machine scope needs `review_by`.
- Expiry: an expired waiver covers nothing and the finding "resurfaces" in brief and doctor (existing `resurfaced`/`expiring`, 14-day heads-up).
- Every waiver, deviation, evaluation and layer rejection is an event (section 7).

### Unattended vs interactive

| | interactive (a person is reachable) | unattended (lane, loop, CI) |
|---|---|---|
| info, recommended | shown, no action | not shown unless asked |
| best-practice | agent complies or records a deviation with a reason; may ask | same; the deviation is logged and surfaces in the operator's next brief ("deviations since your last look") and the completion summary; never blocks the run |
| required / forbidden | fail blocks the gate; to proceed the agent files a waiver REQUEST (pending approval, `RA-request`) and the person answers | fail blocks; the agent files the request and moves to other work (never polls); `unavailable` is recorded and completion stays refused; no auto-waive |
| locked | cannot be requested | cannot be requested |

Auto-approval rules (`RA-policy`, T1, all off by default) may be configured by a company to approve a named waiver pattern with caps; this design adds nothing to them.

## 6. Surfaces

How each strength is shown, within existing budgets. One policy charter replaces the rules charter and the requirements charter (they are one idea with two knobs, see GP-brief).

| Surface | info | recommended | best-practice | required | forbidden |
|---|---|---|---|---|---|
| **Brief** (`brief_max_tokens` 1200) | count only | count only | count; ids only for those that apply to the claimed item | one line each in the charter (id + title, the full text on `rule show`), never trimmed, over cap = doctor finding (`D-requirements-injection`: 500 tokens) | same line, prefixed "FORBIDDEN" |
| **Claim / pre-edit** | - | relevant ones as a line | relevant ones with "follow or record why" | relevant ones, text inline | text inline |
| **Gate** | - | - | `passed` with warning; `complete` refuses (exit 3) until answered: complied or deviated | fail = `failed`; unavailable = `unavailable`; waived is visible | as required |
| **Hook** (pre-commit) | - | - | warn on a mechanical failure | block on a mechanical failure (cheap subset: config/file/pattern only, no models) | block |
| **Doctor** | - | - | deviation count/rate, project-wide opt-outs due for review | unsatisfied/unavailable required, options that can never be evaluated (tool absent), local loosening rejected, expiring and expired waivers, audit-mode policies about to enforce | as required |
| **CLI** | `rule list --strength`, `rule check [--item]`, `rule explain <id>` (which option satisfied, which were tried) | | | | |
| **MCP note** (lean budget) | nothing | nothing | nothing | the existing one-clause pattern: one short clause, only while a required policy is failing or expiring (as `D-DEP-Q6`) | |
| **Agent prompt** | label "info" | label "recommended" | "BEST PRACTICE: follow it, or run `ddflow rule deviate <id> --reason ...`" | "REQUIRED" | "FORBIDDEN" |

Prompt wording is a fixed vocabulary, upper-case words only, one line per record, the same strings in every door (a test pins them), because agents obey consistent labels. Counts line, for example: `Policy: 2 forbidden, 9 required, 14 best-practice, 6 recommended, 3 info (2 waived, 1 expiring); rules: ddflow rule list --strength required`.

## 7. Auditability and events

Reuse existing event kinds where they exist; register new ones only with their first writer (`SE-event-registry`): `policy.evaluated` (record, expression digest, result, satisfied option, tried options with status; throttled: one per record per tree fingerprint), `waiver.*` and `deviation.*` (from `SE-waiver`), `policy.layer_rejected` (a loosening attempt: layer, source, revision, field), `policy.mode_changed` (audit -> enforce on the lock bump). Digests make a replay deterministic. A policy edit is a record edit (history, restore) like any other (`r-everything-managed`).

## 8. Company packs: distribution, pinning, diff review

Graded policy records ride the org bundle (`B-org-bundle`): files under `policies/` with front matter, in the same shape as `.ddflow/rules/`. Pinned in `.ddflow/org.lock` by digest (`B-org-lock`, `CM-packs-verify`); a changed upstream pack is detected by the watch (`CM-packs-watch`) and applied only on the operator's permission with a diff (`D-CM-Q9`, `CM-packs-diff`). The diff is policy-aware (GP-org): it lists per record `strength raised/lowered`, `options added/removed`, `mode`, `locked`, `new/removed`, sorted most severe first, and states which projects' current waivers it would invalidate. The record from an org bundle is read-only in the project (`B-org-kinds`); the project layers its own on top (section 4). Executable parts (a `command` option written by a company) stay inert until a person approves their digest (`D-org-config`). A pack that fails to load: fail open with a loud warning (`D-org-config` 3), but a previously verified cached copy is used first.

## 9. Config, CLI, MCP, prompts

- **Knobs** (all settable by file, CLI, MCP and prompt; every number a knob; enum choices declared, never comment-only, because of bug Beea0744a7b): `[policy].default_mode` (audit|enforce), `[policy].audit_releases` (1), `[policy].waiver_max_days` (90, tighten-only: company can lower), `[policy].deviation_scope_allowed` (item[,project,machine]), `[policy].charter_tokens` (one knob replacing `rules_charter_tokens` and `requirements.charter_tokens`, kept as aliases two releases per the deprecation lifecycle), `[policy].expr_max_depth` (2), `[policy].expr_max_options` (8), `[policy].evaluate_reviewer_options` (true; false makes model-judged options `unavailable` in hooks).
- **CLI (extend `ddflow rule` and the requirement verbs, no new noun):** `rule add|edit --strength --mode --satisfied-by '<expr>' --option id=...`; `rule check [--item|--all]`, `rule explain <id>`, `rule deviate <id> --item --reason`, `rule waive <id> ...` (a request, approved by `ddflow approvals approve`), `rule list --strength`. `ddflow org diff` shows the policy-aware diff.
- **MCP: no new tool.** DECIDED (D-GP-Q9): the actions live in the ONE record family (`ddflow_record`, kind argument, per `D-mcp-new-nouns`), not the `rule` family; the older text below said `rule` family and is superseded. Add actions `check`, `explain`, `deviate`, `waive` and the new params on `add`/`edit`. `MTD-family-spec` (done) declares the matrix and `MTD-profiles` (open) chooses which clients see it; `doctor` family already carries `precommit`. Requirements use the same record; if `requirement` becomes its own family (RQM-surfaces) these actions are declared once and attached to both (one declaration, as `D-record-surface-contract-r2`). A new action adds roughly 0.4-0.6 KB to the family schema; the budget test (`MTD-budget-test`) reports it.
- **Prompt-based management** (`r-company-workflows`): "make signing required for the payments repo but allow gpg, ssh or gitsign" is handled by the agent calling `rule add/edit` with the fields; a company can pin the prompt text (`B-org-kinds`).

## 10. Migration of what exists

No behaviour change on the first release: `strength` is derived, not stored, until set.

| Existing | Becomes |
|---|---|
| rule, enforcement advisory | `recommended` |
| rule/decision, enforcement warn | `best-practice` (override deviate) |
| rule, enforcement block | `required` (override waive) |
| block + no scope ("pinned") | `required`, charter line (unchanged output) |
| requirement MUST / SHOULD / MAY | `required` / `best-practice` / `info` (MAY stays advisory; a requirement author may choose `recommended`) |
| principle (warn + deviate) | `best-practice` |
| org floor (block + none) | `required` + locked |
| org policy `pin|floor|ceiling|require|allow` + `enforce|audit` | the same items as records with `mode`; `allow` lists become `any` over listed values |
| `[enforce].*` block/warn/off | stay knobs (they configure a hook, not guidance); a policy-tier record may reference one through a `config` option; the `strictest` fallbacks stay |
| `gates.required` | stays; a `required` record with `kind = "gate"` option expresses "this gate must pass" per scope |
| commit policy sign off/auto/required | `sign` stays a knob for plumbing commits; "signed by gpg, ssh or gitsign" is the first real any-of policy and the acceptance example |

Upcast: a record file without `strength` reads as the derived value; writing `strength` back writes the matching `enforcement`/`override`, so old ddflow can still read it (golden replay test: an old log folds byte-identical).

## 11. Options compared

| | A. Fields on the guidance record (recommended) | B. A new `policy` kind and evaluator | C. Config-knob policy only (extend `B-org-policy` items) | D. External engine (OPA/Rego, Kyverno-style) |
|---|---|---|---|---|
| Fits decisions | `D-requirements-model` (one kind), `D-goals-principles` (field not kind), `D-shared-engines-r2` (one waiver) | contradicts them | fits | new dependency |
| New engine | no; extends `checks.py`, `waivers.py` | yes | no | yes, plus a language to teach agents |
| Alternatives (any-of) | yes, first class | yes | only as `allow` lists of values | yes (Rego), power far beyond need |
| Covers text rules and checks | yes | yes | no, knobs only | partly |
| Brief/budget | one charter, counts | second charter | none | none |
| Migration | derived view, no change | copy records | none | rewrite |
| Cost | medium (about 9 build tasks) | high | low | high |
| Risk | depends on RQM-check wiring the dormant checks engine | duplicates 5 combiners again | cannot express "sign by A or B or C" for a behaviour; users keep asking | offline/unattended determinism, install burden |

**Recommendation: A**, with C's org-policy items being the same records (no second mechanism) and D left open as a later `opa` check kind (an option may call `conftest` through a `command` option today). Rollout: GP-decide first; ship model and evaluator dark (`audit` everywhere, derived strengths only); turn on the new option expression for the commit-signing example; then company packs; `enforce` per policy after one audit release.

**What would invalidate it:** (1) if the requirement kind lands as a separate store that cannot be adapted into `GuidanceRecord` (`RQM-provider` seam) — then add the fields to both and share only `evaluate`; (2) if `RQM-check` slips so the checks engine stays uncalled — GP-eval then owns the minimal registry; (3) operators find five words confusing (measure with `B-goal-eval` deviation rates; fallback is three words plus a `default-on` flag); (4) any-of is used mostly to reach the weakest option (watch the share of passes by the cheapest option; if high, the policy list was too loose, not the engine); (5) required lines exceed the charter cap in real company packs (then a `scope` requirement is missing, or the cap is wrong); (6) approvals on a single-uid host stay forgeable (`D-RA-4`): waivers are then only as strong as L1, and the proposal says so in doctor rather than claiming more.

## 12. Queue (phase `P-graded-policies`, prefix `GP-`)

Priority medium. Dependencies are real ids; no cycles. The phase is behind the org-config and goals foundations where files overlap, not behind the docs hold.

`GP-decide` -> `GP-inventory` -> `GP-model` -> `GP-eval` -> (`GP-migrate`, `GP-waiver`) -> (`GP-brief`, `GP-surfaces`) ; `GP-org`; `GP-cli` -> `GP-mcp`; `GP-prompt`; `GP-e2e`; `GP-docs`. Details are in each task body (`ddflow show GP-<name>`). Links: related to `P-org-config` (B-org-policy, B-org-layers, B-org-bundle, B-org-lock), `P-requirements` (RQM-model, RQM-check, RQM-strength), `P-goals` (B-goal-deviation), `P-shared-engines` (SE-waiver), `P-commit-messages` (CM-packs-*, signing example), `P-remote-approvals` (RA-policy), `P-deprecation` (DEP window floors use the combiner), `P-batch-gates` (BG-policy floors use the combiner), `P-tool-diet` (family and profiles).

## 13. Questions for the operator (with defaults, for `GP-decide`)

1. Five words (info, recommended, best-practice, required, forbidden)? DEFAULT yes; `forbidden` = required with negative polarity.
2. Is best-practice "answer before complete" (comply or explain, blocks `complete` until answered)? DEFAULT yes for items with a mechanical check or a cited reviewer finding; never blocks the gate itself.
3. `at_least k of n` in the first cut, or only all/any? DEFAULT include (small, same evaluator).
4. May a project add an alternative to a company `any`? DEFAULT no (narrow only); a company can mark an `any` `extensible = true`.
5. New company policy starts in `audit` for one release? DEFAULT yes (`D-CM-Q11`), per record overridable.
6. Waiver cap 90 days and approval tier T2 for project waivers; company-layer exceptions to locked policy T3? DEFAULT yes.
7. Best-practice opt-out scope: item only, or project/machine with `review_by`? DEFAULT item, project with `review_by` <= 180 days, machine off.
8. Merge the two charters into one `[policy].charter_tokens` (default 500)? DEFAULT yes, aliases two releases.
9. Where does the MCP surface live: `rule` family (recommended) or a new `policy` family? DEFAULT `rule` family; no new tool.
10. First acceptance policy: signed commits via gpg or ssh or gitsign? DEFAULT yes.
11. Severity as a separate optional label? DEFAULT later, not in this phase.

### Answers (operator, 2026-10-23)

| Q | Answer | Record | Differs from default |
|---|--------|--------|----------------------|
| 1 | Five words: info, recommended, best-practice, required, forbidden. YES | D-GP-Q1 | no |
| 2 | Best-practice must be answered (comply or explain) before `complete`. YES | D-GP-Q2 | no |
| 3 | `at_least k of n` in the first cut. YES | D-GP-Q3 | no |
| 4 | A project MAY add an alternative to a company `any`. YES | D-GP-Q4 | YES (default was no) |
| 5 | New company policy audits one release first. YES | D-GP-Q5 | no |
| 6 | Waiver cap 90 days; tier 2 project waivers; tier 3 company exceptions to a locked policy | D-GP-Q6 | no |
| 7 | Opt-out per item, or per project with `review_by` <= 180 days | D-GP-Q7 | no |
| 8 | One merged `[policy].charter_tokens`. YES | D-GP-Q8 | no |
| 9 | MCP actions live in the ONE record family (D-mcp-new-nouns), not the `rule` family | D-GP-Q9 | YES (default was the rule family) |
| 10 | Signed commits (gpg or ssh or gitsign) as the first any-of example. YES | D-GP-Q10 | no |
| 11 | Severity label: later | D-GP-Q11 | no |

The operator also confirmed KEEPING `D-audit-calls-2026-10-10` and `D-old-work-behind-core-docs-accepted` (`D-kept-audit-calls-and-old-work-2026-10-23`).

### Open notes (for the operator's review)

- Q4 tension: company floors are tighten-only, and a project-added alternative can weaken a company any-of. Design default that follows the answer: a project may add an alternative by default; a company may mark a requirement `closed` (no added alternatives; a separate axis from the override-none `locked`) or `locked`; every added alternative is reported in the digest and audit; and it must itself be an evaluable named check, so an unavailable option never counts as a pass. Flagged for review: whether `closed` should be the default for `required` items.
