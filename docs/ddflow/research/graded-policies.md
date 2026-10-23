# Graded corporate policies: strength levels and "one of" alternatives

Research for task `GP-research` (phase `P-graded-policies`). Operator request, 2026-10-23, verbatim: "For the centralized corporate policies investigate the option to have soft and strict policies like rules too, like this is recommendation, this is nett practices, this is required, and each of them could be multiple options like it is required to do A or B or C..." ("nett practices" = best practices.)

The proposal built on this is [decisions/graded-policies.md](../decisions/graded-policies.md). External claims are marked **[verified]** (read at the source on 2026-10-23) or **[unverified]** (from memory or secondary reading; re-check before quoting).

## 1. What ddflow already has

Short answer: most of the machinery exists or is already decided and queued. What is missing is one scale, the "one of" expression, and a single place that evaluates it.

### 1.1 Strength vocabularies (six, none shared)

| Where | Vocabulary | Notes |
|---|---|---|
| Guidance record (`services/guidance/record.py`) | `enforcement = advisory \| warn \| block` | Rules default advisory, decisions warn. `ddflow rule add/edit --enforcement --pin` exists (bug B-rule-add-no-enforcement-option is fixed). "Pinned" is derived: block and no scope (`inject.pinned`). |
| Requirements (`D-requirements-model`, RQM-strength) | `strength = MUST \| SHOULD \| MAY` | Mapped to the guidance model: MUST = block + waive, SHOULD = warn + deviate, MAY = advisory. Not built yet (RQM-model open). |
| Principles (`D-goals-principles`, B-goal-deviation) | `override = none \| waive \| deviate`, orthogonal to enforcement | Org floor = block + none; rule = block + waive; principle = warn + deviate; preference = advisory. Not built. |
| Hooks (`[enforce].*`, `config_sections/enforce.py`) | `block \| warn \| off` per knob | Each knob has a declared `strictest` fallback for a bad file value (`KNOB_STRICTEST`). Policy-tier knobs are guarded by B-org-agent-guard (open). |
| Org policy (`B-org-policy`, `D-org-config`) | items `pin \| floor \| ceiling \| require \| allow`, each `enforce \| audit` | Applied last (layer 9 of `D-config-layer-order`). A floor is "a guidance record with enforcement block and override none" (B-org-policy, extended 2026-10-11). Open. |
| Gates | `[gates].required` (list), `GateOutcome` passed/failed/skipped/unavailable, `gates.unavailable_is_failure`, `gates.enforce_order` warn | A gate is in or out of the pipeline; there is no "recommended gate". |

Also: commit policy (`D-CM-2`, `D-CM-Q11`: new policy audits one release then enforces; sign `off|auto|required`), deprecation windows (`D-DEP-Q2` floors by tier stable/beta/experimental), batch gates (`D-BG-Q8` tighten-only company floor), approvals tiers T1 standing policy / T2 per-instance / T3 signed-only and levels L1 terminal, L2 harness, L3 chat/PR, L4 signed (`D-RA-4`, `D-RA-Q6`).

### 1.2 Layering and tighten-only

`D-config-layer-order` is the one order: packaged default < company defaults < user < project < machine-local < env < CLI < session < company ENFORCED. Tighten-only is restated per feature, each with its own combiner: requirements (`D-requirements-layers`: lower layer may only tighten unless a person records a scoped, reasoned, expiring waiver), commit policy (unions, minimum of maxima, conjunction of regexes, maximum of sign mode), batch (`strictest=` floors, `risk_globs_alone` only grows), deprecation (project cannot shorten a company floor), auto-tuning (`D-observe-autotune-stricter-or-cheaper`). There are at least five separate "tighten" implementations in design, plus `config.strictest()` for bad values.

### 1.3 Waivers, deviations, approvals

- `services/guidance/waivers.py` is built and tested: a waiver names a record (and optionally one check), is glob-scoped, carries a reason and an expiry of at most 90 days (`MAX_DAYS`), is approved by a person through `services.approval` by digest, and once expired `resurfaced` shows it again. It has no caller yet (RQM-check notes this).
- `SE-waiver` (open): "ONE waiver record (scope, reason, approver through the approvals engine, expiry)" for decisions, safeguards, goals, tests and gates (`D-shared-engines-r2` 2).
- `B-goal-deviation` (open): a deviation is a waiver in mode `deviation`: item-scoped, reason required, no approval, no expiry; `complete` refuses (exit 3) while a SHOULD finding is neither fixed nor deviated. That is exactly "comply or explain" (section 2.7).
- Other ad hoc waivers: `gate skip --reason`, `[enforce].trailer_waivers`, `Phase-ships` trailer, `D-DEP-Q8` window waivers, `D-test-domains` waiver for unavailable outcomes.

### 1.4 Checks engine (the evaluator that exists)

`services/guidance/checks.py`: a record carries `checks`, small tables each naming a `kind`; evaluators are pure `(check, ctx) -> findings` registered by kind; a check that cannot run is `unavailable`, never a pass; `gate_verdict` folds results with each record's enforcement (block failing -> `failed`; else any unavailable -> `unavailable`; else `passed`; warnings, notes and waived are carried, never silent). Results have a digest "what a cache keyed by the tree fingerprint stores". Gaps: no check kind is registered and nothing calls it (RQM-check wires `command`, `pattern`, `file`, `config`, `reviewer`, `mcp`); checks are a plain list, so the only combination is AND (every check must pass). **There is no any-of, no at-least-k anywhere in ddflow** (grep for `any_of`, `one_of`, `at_least` finds only unrelated knobs).

### 1.5 Surfaces and budgets

- Rules charter (`services/guidance/charter.py`): one line per live rule, id and title, pinned never cut, bounded by `[session].rules_charter_tokens`, at most half the brief; the rest counted and named.
- Requirements charter (`D-requirements-injection`): one line per MUST, `[requirements].charter_tokens` default 500, never trimmed, over-cap is a doctor finding. **Two charters for one idea** (duplicate, section 1.7).
- `inject.py`: rank = pinned, applies-to-all, enforcement (block < warn < advisory), scope specificity; lines tag kind and enforcement; the budget bounds only the unpinned. `ddflow decision applicable <item>` returns the decisions that govern given globs.
- Doctor: notes with `[WARN]` prefixes; unreconciled decisions; hook states. Brief: `session.brief_max_tokens` 1200 default; the brief truncates with a pointer.
- MCP: `D-mcp-tool-diet` makes 24 tools, 15 noun families with an `action` enum. `rule` (add/edit/list/show/search/remove/sync), `decision`, `doctor` and `session` families exist (`MTD-family-spec` done; `MTD-profiles`, `MTD-family-route` open/running). New tools are not allowed (budget is report-only but the diet is the direction).

### 1.6 Company distribution

`P-org-config` (14 tasks, all open): sources, lock (`.ddflow/org.lock`), trust and digest pins, bundle mirroring `.ddflow/`, layers, enforced policy, provenance, surfaces. `D-org-config`: lock-bump updates, signed enforced policy may auto-apply, fail open with a warning when policy cannot load, executable content inert until digest-approved. `CM-packs-*` (init, verify, diff, watch, update): company packs pinned by digest with a human-readable diff and the operator's permission to update (`D-CM-Q9`). `B-org-kinds` makes rules, decisions, principles syncable; a record from an org bundle is read-only in the project (the project layers its own on top).

### 1.7 Duplication and gaps found

1. Six strength vocabularies (1.1) with fixed mappings written in prose in three decisions and no code that holds them all.
2. Two charters (rules, requirements) with two token knobs and two "never trimmed" rules.
3. Three check dialects: guidance `checks`, requirement `verify`, pipeline `define` (B-pipe-model), plus gate commands and commit lint.
4. At least five tighten-only combiners (1.2).
5. Waivers: one engine (built, uncalled), one record (planned), four ad hoc forms.
6. `Config.check` validates only knobs in `_KNOB_CHECKS`; 21 knobs whose values are only in a comment accept typos (bug Beea0744a7b). A strength enum must be a declared choice knob/field, not a comment.
7. No notion of default-on-opt-out ("best practice"), no "forbidden" as a first-class polarity, no alternatives.

## 2. External practice

### 2.1 RFC 2119 [verified]
MUST/REQUIRED/SHALL absolute requirement; MUST NOT absolute prohibition; SHOULD/RECOMMENDED: "there may exist valid reasons in particular circumstances to ignore a particular item, but the full implications must be understood and carefully weighed"; SHOULD NOT mirrors it; MAY/OPTIONAL "truly optional". The key point for ddflow: SHOULD is not "weak MUST"; it carries a duty to weigh and have a reason. RFC 8174 (uppercase only) [unverified] is the usual companion: only capitalised words carry the meaning, which matters for agent prompts.

### 2.2 NIST and ISO control language [unverified]
NIST SP 800-53 separates the control (what) from parameters and assignments chosen by the organisation, and baselines (low/moderate/high) select controls cumulatively; "organisation-defined" values are the tailoring point. ISO 27001 Annex A is applied via a Statement of Applicability: each control is included or excluded with a justification. Both separate "which controls apply" (tailoring, with justification) from "how strong". Lesson: record applicability decisions with a reason, not silent omission.

### 2.3 OPA/Rego and Conftest [partly verified]
Conftest looks for `deny`, `violation` (structured data) and `warn` rules; deny fails, warn is reported separately [verified for the rule types; exit-code behaviour (`--fail-on-warn`) not verified]. Strength is carried by the rule's name, not a field. Rego expresses OR by multiple rule bodies and AND by statements in one body; "at least k" is `count(...) >= k` over a set comprehension [unverified, standard Rego].

### 2.4 Kyverno [verified]
`failureAction: Enforce` blocks; `Audit` allows and records in a PolicyReport (`emitWarning` adds an admission warning). Per-rule action; `failureActionOverrides` per namespace. `anyPattern` is a logical OR (pass if any pattern matches); `deny.conditions.any/all` give OR/AND. A PolicyException references the policy and rule names and a match block; semantics beyond that not read. Lesson: one policy, a mode field, an OR primitive, and exceptions as separate named records.

### 2.5 Kubernetes Pod Security Standards [verified]
Three cumulative levels (privileged, baseline, restricted) and three independent modes (enforce, audit, warn), each settable at a different level on one namespace (e.g. enforce baseline, warn and audit restricted). Exemptions are static (users, runtime classes, namespaces). Lesson: separate LEVEL (what the standard demands) from MODE (what happens on violation), and let a stricter level run in warn mode as a migration path to enforcement. This is the closest model for ddflow's "audit one release then enforce" (`D-CM-Q11`).

### 2.6 GitHub rulesets [partly verified]
Active vs Disabled confirmed; a bypass list of roles/teams/apps; rules from several rulesets combine and the strictest version of a rule wins; org-level rulesets on Enterprise [verified]. The "Evaluate" mode (log what would have been blocked, no enforcement) exists for organisation rulesets on Enterprise [unverified: the page I read did not mention it]. Lesson: strictest-wins combination is the standard answer to layering; a bypass list is an auditable exception mechanism.

### 2.7 SLSA, Sigstore [partly verified]
SLSA Build track L0 to L3, cumulative ("All of Build L1, plus") [verified; page is v1.0, marked retired, v1.2 current]. Levels name what is guaranteed, not what a tool does. Sigstore/gitsign is keyless signing of commits via OIDC identity [unverified]; relevant because "signed commit" has several valid implementations (GPG, SSH, gitsign), the textbook "one of" case; `D-CM-7` already has `sign_verify = git | gitsign`.

### 2.8 Comply or explain [verified]
UK Corporate Governance Code (origin: Cadbury 1992): apply the provision or give a reasoned explanation; it applies to provisions, not to the mandatory principles. FRC guidance: a bare statement that the arrangement is right is not a full explanation; the explanation should show how actual practice still serves the principle; a good explanation carries equal weight to compliance. Sanction is market-based, not legal. This is the exact shape of ddflow's best-practice level: the duty is to answer, the answer is recorded and visible, not to comply.

### 2.9 ADRs, guilds, paved road [unverified]
ADR practice (Nygard) records status and consequences, not enforcement. Spotify "golden path" and Netflix "paved road" are opt-out defaults: the supported way is cheapest and tooled, leaving it is allowed but you own the support cost. Lesson: "best practice" works as a default that is on, easy, and visible when declined; it fails when declining is invisible.

### 2.10 Waivers with expiry [unverified]
Common in AWS SCP/Config exemptions, Azure Policy exemptions (waiver vs mitigated category, expiry date), Kubernetes admission exemptions, and compliance risk-acceptance registers: exception has owner, reason, scope, approver, expiry, and review. ddflow's waiver (reason, glob scope, approver by digest, 90-day cap) already has this set; Azure's split into "waiver" (accepted risk) and "mitigated" (satisfied another way) is the useful distinction for the alternatives design (section 2.11).

### 2.11 "One of" controls in multi-framework compliance [unverified]
SOC 2 criteria (CC series) are objectives; an organisation maps them to its own controls, and several different controls (or compensating controls) can satisfy one criterion; the auditor tests that some control meets the objective. Unified Compliance Framework / OSCAL map one requirement to many implementations (OSCAL "implemented-requirement" with by-components). The model: **requirement = objective; controls = alternatives; evidence names which control satisfied it**. Report the control used, not just a pass.

### 2.12 Policy-as-code boolean expression and thresholds [unverified]
Kyverno `anyPattern`/`any`/`all`, Rego bodies and `count`, Cedar and Sentinel (`any`, `all`, count-based rules), Azure Policy `allOf/anyOf/not`. "At least k of n" is expressed as a count over a list of boolean results in every engine; none ship it as a named primitive except Sentinel-style quantifiers. Design lesson: AND/OR/threshold over a small closed set of named checks is enough; do not embed a general language.

### 2.13 Severity vs strength
Two independent axes in the sources: CVSS/finding severity = impact if violated (informational, low, medium, high, critical); RFC 2119 strength = obligation to follow. A critical-severity finding of a "recommended" practice is still optional; a low-severity violation of a REQUIRED control still blocks. ddflow rejected `priority` as the strength carrier (`D-requirements-model`: orders, does not oblige); severity is the same: it may rank and colour, never oblige.

## 3. Findings that shape the design

1. A graded scale already exists in pieces. A new scale must be a view over `enforcement x override` (one table), not a seventh vocabulary.
2. Best practice is not SHOULD with a different label. It is SHOULD that is on by default for everyone (so applicability is the default), with a cheap, recorded, visible opt-out (comply-or-explain). In ddflow terms: warn + deviate, with the deviation scoped to item, project or machine, and shown to the operator.
3. "Forbidden" is a polarity of a required check (MUST NOT), not a higher strength; keeping it as a spelled word is a UI choice.
4. Alternatives belong to the check expression of a policy, not to the strength. Expression: all-of, any-of, at-least-k-of over named options; every option is an existing check kind.
5. The evaluator needs three-valued logic (pass, fail, unavailable). With `unavailable` never a pass, any-of is satisfied only by a real pass; an unavailable option can make the result unavailable but never green, and cannot hide a real fail in an all-of.
6. Layers: a lower layer may remove options or add conjuncts (narrow), never add an alternative the company did not list (that loosens an any-of) and never lower k.
7. Unattended use must fail closed for required (unavailable, pending approval filed) and fail soft but loud for best practice (agent deviates with a reason, operator sees it).
8. Token budget: only required/forbidden texts are worth inline always; best practice and recommended are counted and fetched by relevance.

## 4. Sources

- RFC 2119, https://www.rfc-editor.org/rfc/rfc2119 [verified]
- Kyverno validate rules, https://kyverno.io/docs/policy-types/cluster-policy/validate/ [verified]
- Kubernetes Pod Security Admission, https://kubernetes.io/docs/concepts/security/pod-security-admission/ [verified]
- GitHub rulesets, https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets [partly verified]
- Conftest, https://www.conftest.dev/ [partly verified]
- SLSA levels, https://slsa.dev/spec/v1.0/levels [verified, retired version]
- Comply or explain (FRC and IoD guidance), found by search 2026-10-23 [verified through summaries]
- NIST SP 800-53, ISO 27001, OSCAL, SOC 2, Azure Policy exemptions, Spotify golden path, Sentinel/Cedar [unverified, from memory]
- ddflow: `D-goals-principles`, `D-requirements-model|layers|injection`, `D-org-config`, `D-config-layer-order`, `D-CM-2|7|Q9|Q11`, `D-DEP-Q8`, `D-BG-Q8`, `D-RA-4`, `D-shared-engines-r2`, `D-mcp-tool-diet`; code `ddflow/services/guidance/{record,checks,waivers,inject,charter}.py`.
