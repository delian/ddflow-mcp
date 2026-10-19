# CodeRabbit vs ddflow: what to adopt (research, 2026-10-17)

Method: official docs (docs.coderabbit.ai, llms.txt index and ~30 pages fetched), two search passes for third-party write-ups, and a read of ddflow's own review machinery (ddflow/services/review.py, ddflow/config_sections/review.py, ddflow/templates/prompts/review_*.md, decisions D-gate-economy-r3, D-phase-review-r2, the open queue of ~1000 tasks). Anything not confirmed is marked UNVERIFIED. Token and time costs in section (c) are my estimates, not measurements.

Note on `ddflow similar`: it returned "nothing scores 0.5" for every idea phrase (threshold too high for short phrases), so overlap was checked by reading and grepping the full task list (`ddflow list task --limit 5000`) plus `ddflow show` on the nearest tasks.

---------------------------------------------------------------------------

## (a) CodeRabbit feature inventory

Base URL for docs: https://docs.coderabbit.ai . Index of every page: https://docs.coderabbit.ai/llms.txt

### Pull-request review
| Feature | What it does | Source |
|---|---|---|
| Walkthrough comment | Main comment on every PR: high-level summary, grouped changed-files summary, sequence diagrams (Mermaid), review-effort estimate 1-5, related issues and PRs, linked-issue assessment, suggested labels and reviewers, status messages, optional poem. Collapsible. | /pr-reviews/walkthroughs.md ; /reference/glossary |
| PR summary | Generated into the PR description at a placeholder (`@coderabbitai summary`); format steerable with `high_level_summary_instructions` (max 3000 chars) | /reference/configuration |
| Sequence diagrams | On by default (`reviews.sequence_diagrams`); `@coderabbitai generate sequence diagram` on demand | /reference/configuration ; /reference/review-commands |
| Line-level comments | Inline findings; severity critical/major/minor/trivial plus info/none; six categories (correctness, security/privacy, data integrity/integration, performance/scalability, stability/availability, maintainability/code quality) | /change-stack/findings.md |
| Committable suggestions | Fenced code suggestions that can be committed from GitHub/GitLab | /change-stack/reviewing.md |
| Incremental review per commit | `auto_incremental_review` (default true); `@coderabbitai review` = incremental, `full review` = from scratch; `auto_pause_after_reviewed_commits` default 5; `pause`/`resume`/`ignore` | /reference/configuration ; /reference/review-commands |
| Review profiles | `quiet` (critical only), `chill` (bugs and security, default), `assertive` (comprehensive) | /reference/configuration ; /reference/glossary |
| Path filters and path instructions | glob include/exclude; per-glob natural-language guidance. Docs state they only steer the review, they do not turn off other features on the same code | /configuration/path-instructions.md |
| AST-based instructions | ast-grep rule YAML files (atomic, relational, composite rules) in a repo directory, each with a message; run as a tool | /configuration/ast-grep-instructions.md |
| Code guidelines auto-detect | Reads AGENTS.md, CLAUDE.md, GEMINI.md, .cursorrules, .cursor/rules/*, copilot-instructions, .windsurfrules, .clinerules/*, scoped to the directory containing the file (case-sensitive; max 50 files per review) | /knowledge-base/code-guidelines.md |
| Auto-review controls | `auto_review`: enabled, drafts, labels, base_branches (regex), ignore_title_keywords, ignore_usernames, description_keyword | /reference/configuration |
| Tone and language | `tone_instructions` (max 250 chars), `language` (ISO code) | /reference/configuration |
| Slop detection | Flags low-quality or AI-generated PRs with a walkthrough warning and optional label; does not block; criteria not published | /pr-reviews/slop-detection.md |
| CI/CD pipeline analysis | Reads failed pipeline output and posts inline fix suggestions on the offending lines (GitHub Actions, GitLab CI, CircleCI, Jenkins, Azure) | /pr-reviews/cicd-pipeline-analysis.md |

### Gating
| Feature | What it does | Source |
|---|---|---|
| Pre-merge checks (built-in) | Docstring coverage (threshold 80), PR title, PR description, linked-issue assessment; each off / warning / error | /pr-reviews/pre-merge-checks.md |
| Custom checks | Natural-language pass/fail rules (Team plan+) run by an agent in a read-only sandbox with ripgrep/ast-grep/shell/web; result Passed / Failed / Inconclusive; cannot run tests, build, or post inline comments; anything needing unavailable capability returns Inconclusive | /pr-reviews/custom-checks.md |
| Request-changes workflow | Submits "request changes" when there are actionable comments; auto-resolves threads when fixed; auto-approves only if latest commit fully reviewed, threads resolved, pre-merge checks pass, HEAD unchanged | /pr-reviews/request-changes-workflow.md |
| Merge readiness | Score with high/medium/low confidence that qualifies the assessment itself ("low-confidence Ready = absence of evidence"); findings triaged into Blockers / High priority / Pending / Advisory with p1-p4 bands | /change-stack/findings.md |
| Security check | GitHub-only check that blocks merge while active security comments are unresolved, configurable threshold | /security/index.md |
| Post-merge actions | Natural-language prompts run in a sandbox after merge (changelog PR, follow-up ticket, report to Linear/Jira/Slack); previewed as checkboxes on the PR | /pr-reviews/post-merge-actions.md |

### Analysis tooling and security
| Feature | What it does | Source |
|---|---|---|
| 50+ linters / SAST | ruff, pylint, eslint, biome, semgrep, opengrep, gitleaks, trufflehog, checkov, trivy, shellcheck, hadolint, actionlint, zizmor, markdownlint, vale, osv-scanner etc.; each toggled under `reviews.tools.<name>` | /reference/configuration ; /tools/list.md |
| AI Deep Scan, attack surface, blast radius, architecture review | Repository-wide security scans outside PRs; reachability tiers (external/internal/unreachable) and exploitability tiers (trivial..theoretical) per finding | /security/index.md |
| Sandboxed repo clone + agentic exploration | Repo cloned into a sandbox; agents explore the codebase for context; separate Review, Verification, Chat, Pre-Merge-Checks, Finishing-Touches agents run in parallel. Filtering details and models are NOT documented | /overview/architecture.md |
| Caching | Encrypted prepared-repo sandbox cache, max 7 days, `disable_cache` | /reference/caching.md |

### Memory and context
| Feature | What it does | Source |
|---|---|---|
| Learnings | Created from chat replies to review comments, imported from files (`@coderabbitai add a learning using docs/coding-standards.md`), or inferred by the pipeline; scope local / global / auto; `approval_delay` 0-30 days for admin review; credentials redacted before storage; editable by dashboard, chat or API | /knowledge-base/learnings.md ; /guides/learnings |
| Knowledge base | Learnings + code guidelines + Jira/Linear/GitHub issues + past PRs + MCP servers + web search, each with `usage`/`scope` knobs | /reference/configuration ; /knowledge-base/index.md |
| Multi-repo analysis | Compares PR against linked repositories (API contracts, shared libs, DB schemas); manual `linked_repositories` (max 50) or auto-linking (Team+) | /knowledge-base/multi-repo-analysis.md |
| Issue enrichment | Duplicate and similar-issue detection, related PRs, suggested assignees, labels | /issues/enrichment.md |
| Issue Planner / CodeRabbit Plan | Issue or PRD to a codebase-aware Coding Plan, refinable, with agent-ready handoff prompts; auto-planning by label | /plan/index.md ; /reference/configuration |

### Finishing touches (act on the PR)
| Feature | What it does | Source |
|---|---|---|
| Autofix / "fix comments" | Applies fixes for unresolved CodeRabbit threads as commits or a stacked PR, with repo setup plus build verification; `autopilot` loops on findings and CI | /finishing-touches/autofix.md ; /reference/review-commands |
| Fix CI, resolve merge conflicts | Agent investigates failing checks / conflicts and pushes a fix | /reference/configuration (`finishing_touches`) |
| Unit test generation | Generates tests using the repo's framework and mocking conventions; delivered as a new PR (recommended) or a commit; fixes CI issues of the generated tests (missing deps, imports). Open beta | /finishing-touches/unit-test-generation.md |
| Docstring generation | Command or checkbox; also feeds the docstring-coverage check | /reference/review-commands |
| Simplify | Simplifies changed code; default off | /reference/configuration |
| Custom recipes | Named reusable instructions (max 10 000 chars) triggered by comment | /finishing-touches/custom-finishing-touches.md |

### Pre-PR surfaces and reporting
| Feature | What it does | Source |
|---|---|---|
| CLI and IDE review | Reviews uncommitted changes before a PR; CLI has an `--agent` mode emitting NDJSON findings (severity, file, startLine/endLine, `codegenInstructions` for the agent), completion events and exit codes; docs warn "receiving findings does not prove the whole review completed" | /overview/ide-cli-review.md ; /cli/agent-mode.md |
| Claude Code / Cursor / Codex integrations | Plugin or skill so a coding agent runs review, reads findings, fixes, repeats | /cli/claude-code-integration.md |
| Triage view | Cross-repo queue of open PRs ranked by value and risk | /triage/index.md |
| Dashboard and metrics | Acceptance rate of comments (also by severity and category), time to merge, time to last commit, learnings usage, path-instruction adoption, pre-merge check results, finishing-touch runs | /guides/dashboard-metrics.md |
| Scheduled reports | Daily / weekly / monthly PR reports (stale after 168 h) to email, Slack, Discord, Teams | /guides/scheduled-reports.md |
| Config tooling | `@coderabbitai configuration`, `generate configuration`, `emit path instructions` (opens a PR with suggested path rules), `generate project vocabulary`, `configuration override` for a single PR; inheritance and central configuration | /reference/review-commands ; /configuration/central-configuration.md |

### Chat commands (selection)
review, full review, pause, resume, ignore, summary, generate docstrings / unit tests / sequence diagram, fix comments, fix comments stacked pr, autopilot (+resume/stop), fix-ci, approve, resolve, rate limit, configuration, help. Source: https://docs.coderabbit.ai/reference/review-commands.md

### Known weaknesses (sourced)
- An independent audit cited by aicodereview.cc: 35% of comments genuinely useful, 36% noise (21% nitpicks, 15% useless). Causes named: review on every push (eight commits give eight reviews) and a coverage-over-signal design. Fixes recommended there: pause/resume during iteration, `tone_instructions`, `path_filters`, `request_changes_workflow`. https://aicodereview.cc/blog/coderabbit-reduce-noise
- Search summary of one developer's four-month tally: 270 legitimate issues vs 70 false positives (~1 in 5 needs dismissal). Vendor-neutral status of that tally UNVERIFIED (single practitioner account, via https://vantaige.io/ai-tool/coderabbit).
- Martian benchmark as summarised by a third-party comparison: CodeRabbit F1 51.5%, precision 50.5%, recall 52.5% over 317k reviews; Greptile precision 66.2% but recall 40.4%. I could not open the page (HTTP 429), so these numbers come from a search-result summary of https://www.morphllm.com/comparisons/coderabbit-vs-greptile : UNVERIFIED against the primary benchmark.
- Stale reviews after a config change need a manual full review (aicodereview.cc, same page).
- Custom checks cannot run tests or build, so many return Inconclusive (own docs, /pr-reviews/custom-checks.md).
- Slop detection criteria unpublished and "still being tuned" (own FAQ, /pr-reviews/slop-detection.md).
- Plan gating: custom checks, multi-repo auto-link and post-merge actions are higher-tier features (docs above; pricing page not read, plan names UNVERIFIED).
- Not found in the docs I read: a documented "code graph" product feature (the architecture page speaks of 50+ analyzers plus agentic exploration and a knowledge base). Treat "code graph" as UNVERIFIED.

---------------------------------------------------------------------------

## (b) CodeRabbit feature -> ddflow

Evidence for ddflow column: review.py/config_sections/review.py (rounds, delta, combined review, triage with probes, chunking, STATUS contract), review_system.md (HIGH/MEDIUM/LOW findings, "report nothing if uncertain", records delivered as context), gate list (research, rules, rubber_duck, critic, standards, ci, unit_tests, bug_hunt, dedupe, verify, docs, merge), `ddflow gate verify` mutation checks, D-gate-economy-r3, D-phase-review-r2, open phases listed.

| CodeRabbit feature | ddflow status | Where |
|---|---|---|
| PR summary / walkthrough | Partial | FL-receipt (proof-of-work summary of an item; open), `ddflow report ITEM`, DX-agent-final-summary (`report --brief`, open). No file-grouped walkthrough, no diagram, no effort estimate. |
| Sequence diagrams | Gap | Nothing queued. Low value for ddflow (see rejected). |
| Line-level findings | Have | services/review.py findings with file:line, digest, HIGH/MEDIUM/LOW |
| Review effort estimate | Have (different form) | Risk tiers (tier:fast / tier:deep) and review depth by size and risk, D-gate-economy-r3 (b) |
| Chat commands in PR | N/A | ddflow is not PR-centric; CLI/MCP are the commands. PR sync exists (`pr_sync`, `pr_threads`) |
| Path instructions / path filters | Have | Rules and lessons delivered by glob into review context; `[reviewer].extra_rules`; DX-review-exclude (open) for skip globs |
| .coderabbit.yaml | Have | `.ddflow/config.toml`, conf.d plan P-config-engine |
| Review profile (chill/assertive) | Partial | Fixed strict prompt in review_system.md; no per-project profile or severity floor. B-pi-review-lens (open) makes lens data but not a volume profile |
| Learnings | Partial | Lessons/rules/memory exist and are delivered by glob. Missing: a store of refuted review findings used to quiet later reviews (gap). P-lessons.recurrence/promote (open) cover lessons recurring, not false positives |
| Linters / SAST | Partial | `standards` gate (roborev); P-stack-tools (SC-catalogue, SC-gate-binding, open); IA-sarif ingest (open). Not yet fed to the LLM reviewer as pre-pass |
| Secret scanning | Gap | SE-untrusted-scan (secrets in untrusted text) was ABANDONED; SC-catalogue lists security tools but nothing binds a diff secret scan to a gate yet |
| ast-grep structural rules | Partial | SC-lsp-ast registers ast-grep as an agent companion (open); BL-structural phase is done but unrelated (UNVERIFIED what it covers). No project rule directory run in the gates |
| Pre-merge: docstring coverage | Gap | review prompt says "do not report missing docstrings"; docs gate is phase-level docs drift (`ddflow docs check`) |
| Pre-merge: title / description | Have (different) | `verify` gate (completion verification), plan check for task bodies, `docs check` |
| Pre-merge: issue assessment | Have | `verify` gate checks the item's acceptance against the diff |
| Custom checks (NL, Passed/Failed/Inconclusive) | Partial | Rules delivered to reviewers; a violation is a normal finding (review_system.md). No per-rule verdict, no inconclusive state tied to a rule |
| Pre-merge modes off/warn/error | Have | Gate pipeline with skip reasons; "warn by default, block per decision" pattern in D-decision-management |
| Request-changes + auto-resolve threads | Partial | Superseded findings, fix_round_free. DX-review-delta-superseded and DX-review-round2-stale-findings (open) are exactly CodeRabbit's "auto-resolve fixed threads" |
| Merge readiness with confidence | Partial | `gate status` gives per-gate outcomes; unavailable/partial/unreviewed chunk handling exists. No single readiness verdict with a confidence that reflects review coverage |
| Incremental review per commit | Have | delta rounds, `delta_default`, fix_round_free, `--chunk` |
| Pause/resume reviews | Have (not needed) | Reviews are explicit calls, never automatic on push |
| Review budget / noise control | Have | max_rounds, settle_all_refuted, combined_under_lines |
| Cross-family second opinion | Have (ahead) | Rule: reviewer from a different model family; CodeRabbit publishes no such control |
| Verification agent (checks findings before posting) | Partial | Triage with probes (manual); `ddflow gate verify` mutations. No automatic verifier pass |
| Autofix / fix comments / committable suggestions | Gap | Findings have no fix text or patch field; agents fix by hand |
| Fix CI | Have | `ddflow ci`, merge CI check, bug auto-filing |
| Unit test generation | Partial | unit_tests gate runs tests; TF-flows-code (unit-gen-filtered), TF-filters, TF-mutation (open) |
| Docstring generation | Gap (low) | none |
| Simplify finishing touch | Gap (low) | `/simplify` exists as an agent skill, not a ddflow pass |
| Custom recipes | Have | `ddflow job add/run`, triggers, schedule |
| Issue-to-PR planning | Have | `plan check/apply`, P-plan-modes, P-sdd |
| Issue enrichment (dupes, similar) | Have | `ddflow dupes`, `ddflow similar`, add-time duplicate/contradiction check |
| Multi-repo analysis | Partial | P-constellation (sister projects, shared specs; open) |
| Knowledge base (Jira/Linear/MCP/web) | Have | external sync, companions (context7, codeguide), recall/RAG |
| Code-guideline auto-detect | Have | P-ai-import / OB-* onboarding imports AGENTS.md, CLAUDE.md, cursor rules etc. as rules |
| IDE/CLI pre-commit review | Have | `ddflow precommit`, `ddflow review --branch/--commit` |
| Agent-mode NDJSON for coding agents | Partial | `--json` exists; no per-finding fix instructions or completion contract line for the author agent |
| Slop detection | Partial | `dedupe` gate, SC-guards (new suppressions, open), AG-deps-guard (open); no "slop" lens |
| Post-merge actions | Partial | `version cut --changelog`, jobs/triggers; no per-merge declared follow-ups (TODO to task) |
| Triage view (PR queue by risk) | Have | `next`, `board`, tiers, governor |
| Dashboard (acceptance rate) | Partial | `ddflow metrics`, FL-scorecard (per agent, open), DX-reviewers-bench (seeded recall, open). Missing: production precision per reviewer/gate/severity from triage data |
| Scheduled reports | Have | `cadence`, `schedule`, `metrics forecast`, ID-retro |
| Tone / language | Have (not needed) | prompts overridable in .ddflow/prompts |
| Security architecture / blast radius | Partial | `security/engine/hook/lease` glob pins force full pipeline; no reachability tiers |

---------------------------------------------------------------------------

## (c) Ranked shortlist (12)

Ordering is by quality-or-maintenance gain per unit of cost and risk. "Existing machinery" always means code already in the repo.

### 1. Review-finding learnings: a store of refuted findings, delivered to later reviews
- What: when `review triage --refuted --probe ...` records a finding false, also store a `review-note` (kind: known-non-defect) carrying digest, location glob, title and the probe. Later reviews of items touching the same globs get those notes in context ("already examined and shown false, do not re-raise unless the code changed"), and an identical-digest re-raise is auto-marked `pre-refuted` instead of costing a triage turn.
- Why: CodeRabbit's biggest published weakness is noise; its answer is learnings. ddflow already measures it: refuted findings sit in gate evidence (`ddflow gate list --refuted`) and delta rounds re-raise them (DX-review-delta-superseded, DX-review-round2-stale-findings).
- Design: new record kind under the lessons engine (scope by globs, expiry, verify via the recorded probe). Hook: context builder in services/review.py (the same place that delivers rules and decisions). Config: `[review].learn_refuted = off|note|auto_mark` (default note), `[review].learn_ttl_days`, `[review].learn_max_notes`. CLI `ddflow review notes list|drop`; MCP via ddflow_configure plus a list tool. A note is dropped when its cited file hash changes (reuse the superseded-by-rewrite check). HIGH findings are never auto-marked, only noted.
- Cost: no extra LLM calls; adds roughly 100-300 tokens per relevant note to each review prompt, capped by the budget knob. Build time: 3-4 tasks.
- Risk: a wrong refutation silences a real bug. Mitigation: require a probe that names a test or run (fix-Bb049f0ae06 already tightens `--probe`), expiry, never auto-mark HIGH, show "N findings pre-refuted" in the output.
- Depends: fix-Bb049f0ae06 (probe validation), DX-review-delta-superseded (shares the superseded check), P-lessons.scope (scoping). Cross-check with P-lessons.recurrence so a note and a lesson do not duplicate.

### 2. Finding confidence and a review profile knob
- What: reviewer emits `CONFIDENCE: high|medium|low` per finding (the prompt already asks the reviewer to stay silent when uncertain, so this makes the residue visible). `[review].profile = quiet|standard|strict` sets the minimum severity that can fail a gate and which lens text is used; below the floor a finding is recorded as `advisory`.
- Why: CodeRabbit's quiet/chill/assertive profiles and its confidence-qualified merge readiness answer "how much to trust this". The Martian figures (precision about 50%) suggest triage time is the real cost of LLM review.
- Design: extend FINDING line in review_system.md with a confidence field, parsed in services/review.py next to severity; `finding_digest` unchanged (confidence is not part of identity). Config: `review.profile`, `review.fail_floor` (HIGH|MEDIUM|LOW) in config_sections/review.py, strictest-wins like `on_exceed`. Triage list orders by severity then confidence. Reuse the B-pi-review-lens gate lens field for per-gate profile.
- Cost: negligible tokens (one word per finding). 2-3 tasks.
- Risk: models inflate confidence; a floor of LOW can hide real bugs. Mitigation: default profile = today's behaviour (no floor), report the advisory count, floor can only be raised by the operator (D-reviewer-trust style).
- Depends: B-pi-review-lens (lens as data) for the per-gate lens; otherwise standalone.

### 3. Review precision analytics from triage data
- What: `ddflow metrics review`: per reviewer, gate, severity and path glob, the share of findings confirmed vs refuted vs unresolved, rounds per item, tokens per confirmed finding, runaway/off-contract rate.
- Why: CodeRabbit's dashboard tracks comment acceptance rate by severity and category; ddflow has the same data in the log but no view. It makes reviewer choice and the round budget evidence-based (today a hand-written 90-line benchmark was needed, per DX-reviewers-bench).
- Design: pure log fold in the existing metrics service (B-gap-metrics is done); CLI/MCP read-only; output feeds `reviewers bench` and a doctor NOTE when a reviewer's refuted share exceeds a knob (`metrics.review_refuted_warn`, default 0.6). Optional weekly line in the existing cadence report.
- Cost: no tokens; one query over events; 2 tasks.
- Risk: small samples mislead; label n and show intervals. A reviewer looks bad because the author was slow to fix; use verdict not outcome.
- Depends: B-gap-metrics (done); complements DX-reviewers-bench and FL-scorecard (do not duplicate scorecards: this is per reviewer, that is per author agent).

### 4. Natural-language checks with a three-valued verdict (`check` records)
- What: project-declared checks, each: id, instruction (one concern), globs (when it applies), mode `off|warn|error`, and result `passed|failed|inconclusive` with cited evidence (file:line). Examples: "no secrets or PII in log calls", "every new CLI verb has an MCP twin and a help-topic entry", "a new config knob appears in docs".
- Why: CodeRabbit custom checks turn recurring review comments into explicit pass/fail rules. ddflow rules are advice that a reviewer may or may not cite; a check yields a recorded verdict. `inconclusive` maps directly onto ddflow's "unavailable is never passed" rule.
- Design: new record kind (full record contract RC1-RC12 per D-universal-principles); evaluated by a cheap reviewer through the existing reviewer pool and STATUS-block contract, one check per request (small prompts), applicable only when the diff matches the check's globs; hooks into the `standards` gate as sub-results (no new gate in the default pipeline) or an optional `checks` gate. Config: `[checks].default_mode = warn`, `[checks].max_per_item`, CLI `ddflow check add|list|run`. Different-family rule does not apply to checks (they are not reviews), but recorded reviewer id is.
- Cost: one short LLM call per applicable check (roughly 1-3k tokens each, 10-60 s); capped by `max_per_item`. 5-6 tasks.
- Risk: vague instructions produce flaky verdicts; CodeRabbit's own docs say vague checks come back inconclusive. Mitigation: warn by default, store a seeded pass/fail example pair per check and run it in `check verify`.
- Depends: B-pi-review-lens, B-reviewer-pool, record contract (P-core-records). Overlaps conceptually with P-lessons.promote ("promote into a check"): make this the target record kind rather than a second one.

### 5. Fix instructions on findings and an agent-mode findings feed
- What: reviewer adds `FIX:` (a minimal concrete change, optionally a unified diff) to each finding; `ddflow review findings ITEM --agent` prints NDJSON per confirmed finding (id, severity, confidence, file, start/end line, fix text) plus a final `complete` line stating whether every chunk was reviewed and the exit code; `--apply-check` tries the diff in a scratch worktree and runs the affected tests before the author agent sees it.
- Why: CodeRabbit CLI `--agent` mode and autofix show the value of a machine-readable loop (the docs also stress that findings do not prove the review completed, which ddflow already encodes as unreviewed chunks). It cuts the author's turns between "finding" and "fix".
- Design: parse `FIX:` in services/review.py (field on Finding, not in digest); new reader in api/review_findings.py; scratch apply reuses worktree service and `testscope` selection from the unit_tests gate. No auto-commit (the author agent decides), which keeps D-failed-review-gate intact.
- Cost: +50-200 tokens per finding output; apply-check costs one affected-test run (seconds to minutes, no LLM tokens). 4 tasks.
- Risk: plausible-looking wrong patches; agents may apply without understanding. Mitigation: the fix is a hint, triage still demands a probe; apply-check result is shown.
- Depends: DX-review-findings-show (done), review triage machinery.

### 6. Automatic finding verifier pass (advisory)
- What: after a review round, a cheaper or different reviewer is asked, per HIGH/MEDIUM finding, to produce a small falsifying or confirming probe (a command or test snippet) and run it in a sandbox; result `verified|refuted-by-probe|inconclusive` is attached to the finding as a hint for triage. Never settles a gate by itself.
- Why: CodeRabbit lists a Verification agent in its pipeline (detail UNVERIFIED); ddflow's triage already requires probes but a human or agent writes them every time (see settle_all_refuted).
- Design: new step in api/review.py after parse; reuses reviewer pool, `review triage` probe field format, `services/sandbox` or worktree runner (for safety use the existing command-reviewer git-state guard). Config: `[review].verify_findings = off|advisory` (default off), `[review].verify_max_findings = 5`, `[review].verifier` (reviewer name).
- Cost: about 3-8k tokens and 30-120 s per finding; capped by knob. 4-5 tasks.
- Risk: executing LLM-written probes (safety); false "refuted" hints biasing triage. Mitigation: sandboxed worktree, read-only default, advisory label, hint never auto-triages.
- Depends: idea 2 (confidence slot) and B-pi-review-panel (agreement counts can feed the same field). Lower priority until 1-3 are in.

### 7. Diff-scoped tool findings as reviewer pre-pass context
- What: run the project's configured analyzers (ruff, mypy, semgrep, ast-grep, gitleaks) on the changed files before the LLM review, give their findings to the reviewer as "tool output, do not repeat, confirm or explain only if you disagree", and record them as gate evidence.
- Why: CodeRabbit runs 50+ analyzers and lets the LLM filter and explain them; this raises recall on mechanical defects and lets the LLM spend tokens on logic. In ddflow the `standards` gate runs roborev separately and the reviewer never sees tool output.
- Design: reuse SC-catalogue/SC-gate-binding (tool to gate role) and IA-sarif's finding ingest; new context section in review_user.md; config `[review].tool_context = off|on`, `[review].tool_context_max_lines`. Secret scan (gitleaks/trufflehog) is the first tool because SE-untrusted-scan was abandoned and no gate covers diff secrets.
- Cost: tool runtime (seconds); +200-1500 prompt tokens per review. 3 tasks after SC-gate-binding lands.
- Risk: tool noise floods the prompt; line cap and per-tool severity floor handle that.
- Depends: SC-catalogue, SC-gate-binding, IA-sarif (all open). Standalone secret-scan slice can ship earlier.

### 8. Merge-readiness verdict with coverage confidence
- What: `ddflow ready ITEM` (also in `gate status` and the receipt): READY / NOT READY / UNKNOWN with confidence high|medium|low derived mechanically: high only if all gates have a non-hand-recorded outcome, every review chunk was reviewed, no gate passed on refutation without a test probe, no unavailable gates; low when any review was partial, delta-only, or hand-recorded.
- Why: CodeRabbit's "low-confidence Ready is absence of evidence" is the same principle as ddflow's unavailable rule, stated as one number the operator or the phase-end review can read. DX-gate-auto-record-mechanical shows gates recorded "by hand" with grep evidence on nearly every item; the verdict makes that visible.
- Design: pure function over gate evidence (services/gates/evidence.py); surfaces via gate status, brief and report; knob `[flow].require_confidence = off|medium|high` for complete/merge (default off = informational). Feeds DX-phase-review input list.
- Cost: no tokens; 2 tasks.
- Risk: becoming one more thing agents game; keep it derived, not recordable.
- Depends: DX-gate-auto-record-mechanical (raises the share of non-hand evidence), D-failed-review-gate.

### 9. Deterministic docstring / public-API doc coverage ratchet
- What: a changed-lines check: new or changed public functions, classes and CLI verbs lacking a docstring or help text, counted against a threshold with `off|warn|error` mode; ratchet so coverage may not fall.
- Why: CodeRabbit's docstring-coverage pre-merge check (default 80%). For ddflow it keeps `docs check` and generated help honest at no LLM cost; maintenance gain accrues over time. Reviewers are told not to report docstrings, so nothing covers it today.
- Design: AST pass (Python first; other languages via the stack catalogue) in the `standards` gate or `docs check --diff`; config `[docs].docstring_mode = off` default, `docstring_threshold`, `docstring_scope = changed|all`; reuses B-docs-check-command and the constants ratchet pattern (docs/ddflow/constants-exemptions.toml).
- Cost: no tokens, sub-second. 2 tasks.
- Risk: vacuous docstrings written to satisfy the count. Mitigation: warn by default and a minimum-length heuristic.
- Depends: B-docs-check-command (done).

### 10. TODO/FIXME follow-up capture at merge
- What: at `merge`, scan added lines for TODO/FIXME/XXX/HACK; each must reference a task or bug id, or ddflow files a PROPOSED task (never auto-confirmed) and rewrites the marker with the id in a follow-up commit suggestion.
- Why: CodeRabbit post-merge actions "file follow-up ticket if the PR references a known TODO". Prevents orphan TODOs rotting; maintenance benefit.
- Design: mechanical pre-merge check in services/gates (warn default), proposal intake reuses PL-proposed-intake semantics (open) and `bug found`/`task add --proposed`. Config `[merge].todo_markers`, `[merge].todo_mode = off|warn|propose`.
- Cost: no tokens. 2 tasks.
- Risk: TODOs in vendored or test fixture text; reuse exclude globs.
- Depends: PL-proposed-intake, DX-review-exclude (for ignore globs).

### 11. Lenses for maintainability and anti-slop on demand
- What: shipped optional lenses `maintainability` (dead code, duplicate helper, restated-code comments, over-broad exception handling, unused parameters) and `simplify`, run as an off-by-default pass after gates pass; findings are advisory.
- Why: CodeRabbit's maintainability category, Simplify touch and slop detection. ddflow's critic/rubber_duck lenses deliberately hunt defects; nobody reviews structure until the phase-end review.
- Design: ship as prompt files for B-pi-review-lens (`review_lens/maintainability.md`); `ddflow review ITEM --lens maintainability`; `[review].extra_lenses = []`; counted in no round budget (advisory), ledger as `review.advisory`. Pair with SC-guards (suppression counter) for the mechanical half.
- Cost: one extra review (about the same tokens as one critic round, 20-120 s on the LAN model); only when enabled.
- Risk: nitpick flood, which is exactly CodeRabbit's complaint. Mitigation: advisory only, HIGH not allowed in this lens, shown as a count.
- Depends: B-pi-review-lens (blocking), SC-guards.

### 12. Change walkthrough in the receipt (file groups, flow diagram, effort)
- What: extend FL-receipt with a generated "what changed" section: files grouped by role with one line each, an optional Mermaid sequence/flow diagram for items touching runtime flows, and a review-effort rating from existing risk tier and diff size.
- Why: helps the operator and the phase-end reviewer read an item; CodeRabbit walkthrough is its most-liked artifact (popularity claim UNVERIFIED, no source read). Pure maintenance and handoff value.
- Design: template in the receipt record kind; a cheap summarise-role model (GV-roles search/summarise), cached by head sha; off by default; `[receipt].walkthrough = off|files|files+diagram`. Never invents facts: only diff and gate data.
- Cost: about 2-5k tokens per item when on. 2 tasks.
- Risk: a wrong summary misleads. Keep to descriptive file lines; diagram is opt-in.
- Depends: FL-receipt, GV-roles. Last in rank because it improves reading, not correctness.

---------------------------------------------------------------------------

## (d) Explicitly rejected

| CodeRabbit feature | Reason |
|---|---|
| Auto-review on every push, auto_pause after N commits | ddflow reviews are explicit, budgeted (max_rounds). Auto-review is the root of CodeRabbit's documented noise. |
| `@coderabbitai` chat commands in a PR thread, mention-driven chat | PR-centric. ddflow's surfaces are CLI/MCP; `pr_sync` and `pr_threads` already bridge PR comments where a project uses PRs. |
| Learnings written by free-form chat and inferred silently | Silent inference conflicts with D-reviewer-trust and the record-the-operator's-words rule. Idea 1 keeps a probe-backed, expiring, listable variant instead. |
| Autopilot / auto-commit of fixes without the author | Breaks claim/lease ownership and the independence rule (author must not both fix and approve unseen). Idea 5 gives hints, not commits. |
| Request-changes bot approval of PRs | ddflow completion is decided by gates and the operator; an LLM approver adds nothing over `complete`. |
| Sequence diagrams on every PR | Token cost for little defect-finding value; kept as opt-in only inside idea 12. |
| Poem, fortune, ASCII art, in-progress jokes | Noise by design. |
| Unit-test generation as a PR-side finishing touch | ddflow already plans TF-filters/TF-flows-code with build/pass-N-times/mutation filters, which is stricter than CodeRabbit's CI-retry loop. Do not add a second generator. |
| Docstring generation | Low value, invites vacuous docstrings. The coverage ratchet (idea 9) targets the maintenance goal without generating text. |
| Slop labelling of external PRs | ddflow has no inbound external contributors in its model; SM-gh-triage handles reports. |
| Multi-repo linked analysis as a copy | P-constellation already owns this with shared specs; revisit only after it lands. |
| Security reachability / exploitability tiers, AI Deep Scan | Large separate product (usage-billed at CodeRabbit). Out of scope for a work queue; IA-sarif plus SC tools cover ingest of real scanners. |
| Merge/branch rules by labels, base branches, title keywords | ddflow flow/branching strategy is data already (P-branching, BR-*). |
| Scheduled PR-staleness reports (168 h) | ddflow has aging detectors under P-observe.detectors and `cadence`. |
| Tone/language settings | Prompts are already overridable per project in .ddflow/prompts. |
| Cloud sandbox cache for repos | ddflow runs locally in git worktrees. |
| Issue enrichment | `dupes`, `similar` and add-time dedupe already do it. |

---------------------------------------------------------------------------

## (e) Suggested phase and tasks (not filed)

Phase: `P-review-quality` , "Review quality and signal: learn from triage, show confidence, verdicts as data, fewer wasted rounds". Placed after the in-flight review fixes; reuses P-lessons and P-stack-tools primitives rather than duplicating them.

Existing open tasks to schedule FIRST because the whole phase assumes them (not new): DX-review-delta-superseded, DX-review-round2-stale-findings, fix-Bb049f0ae06, DX-review-exclude, DX-review-manifest, B-pi-review-lens, DX-gate-auto-record-mechanical.

| Id | Title | Needs | Globs |
|---|---|---|---|
| RQ-note-record | Review-note record kind (known non-defect): digest, globs, probe, expiry; full record verbs | fix-Bb049f0ae06, P-lessons.scope | ddflow/services/review_notes.py, ddflow/core/events.py, tests/test_review_notes.py |
| RQ-note-capture | `review triage --refuted` writes a note; drop on file-hash change | RQ-note-record, DX-review-delta-superseded | ddflow/api/review.py, tests/test_review_note_capture.py |
| RQ-note-deliver | Deliver notes into review context under a budget; pre-refute identical digests; `[review].learn_refuted` knobs | RQ-note-capture | ddflow/services/review.py, ddflow/config_sections/review.py, tests/test_review_note_deliver.py |
| RQ-confidence | `CONFIDENCE:` field in FINDING lines, parser, triage ordering | DX-review-manifest | ddflow/templates/prompts/review_system.md, ddflow/services/review.py, tests/test_review_confidence.py |
| RQ-profile | `[review].profile` and `fail_floor`; advisory findings do not fail a gate | RQ-confidence, B-pi-review-lens | ddflow/config_sections/review.py, ddflow/api/review.py, tests/test_review_profile.py |
| RQ-metrics | `ddflow metrics review`: precision by reviewer, gate, severity, glob; doctor note on high refuted share | B-gap-metrics | ddflow/services/metrics*.py, ddflow/api/metrics.py, tests/test_metrics_review.py |
| RQ-fix-field | `FIX:` field on findings and the `review findings --agent` NDJSON feed with completion line | RQ-confidence | ddflow/services/review.py, ddflow/api/review_findings.py, tests/test_review_findings_agent.py |
| RQ-fix-apply-check | `--apply-check`: apply suggested diff in a scratch worktree, run affected tests | RQ-fix-field | ddflow/services/review_fixcheck.py, tests/test_review_fixcheck.py |
| RQ-check-record | `check` record kind: instruction, globs, mode, shipped seeded examples | P-core-records | ddflow/services/checks/record.py, tests/test_checks_record.py |
| RQ-check-run | Evaluate checks through the reviewer pool; passed/failed/inconclusive; hook into the standards gate | RQ-check-record, B-pi-review-lens | ddflow/services/checks/run.py, ddflow/services/gates/standards*.py, tests/test_checks_run.py |
| RQ-check-promote | P-lessons.promote target: turn a recurring lesson into a check; `check verify` with the seeded pair | RQ-check-run, P-lessons.promote | ddflow/services/lessons/promote.py, tests/test_check_promote.py |
| RQ-verifier | Advisory finding verifier (`[review].verify_findings`), sandboxed probe run | RQ-confidence, B-pi-review-panel | ddflow/services/review_verify.py, tests/test_review_verify.py |
| RQ-toolctx-secrets | Diff-scoped secret scan (gitleaks/trufflehog companion) as a standards sub-result | SC-gate-binding | ddflow/services/gates/secrets.py, tests/test_gate_secrets.py |
| RQ-toolctx-prepass | Tool findings as reviewer pre-pass context with caps | RQ-toolctx-secrets, IA-sarif | ddflow/services/review.py, ddflow/templates/prompts/review_user.md, tests/test_review_toolctx.py |
| RQ-ready-verdict | `ddflow ready ITEM`: derived verdict with coverage confidence; `[flow].require_confidence` | DX-gate-auto-record-mechanical | ddflow/services/gates/readiness.py, ddflow/api/gates.py, tests/test_readiness.py |
| RQ-doc-coverage | Docstring/help coverage ratchet for changed public symbols | B-docs-check-command | ddflow/services/docscheck_coverage.py, ddflow/config_sections/docs.py, tests/test_doc_coverage.py |
| RQ-todo-capture | TODO/FIXME with no id at merge: warn or file a PROPOSED task | PL-proposed-intake, DX-review-exclude | ddflow/services/gates/todo_capture.py, tests/test_todo_capture.py |
| RQ-lens-maintain | Shipped maintainability and simplify lenses, advisory only | B-pi-review-lens, SC-guards | ddflow/templates/prompts/review_lens/maintainability.md, tests/test_review_lens_maintain.py |
| RQ-walkthrough | Receipt walkthrough section (file groups, optional diagram, effort) | FL-receipt, GV-roles | ddflow/services/receipt_walkthrough.py, tests/test_receipt_walkthrough.py |
| RQ-acceptance | Acceptance with fakes: a refuted finding is noted and not re-raised; confidence floor demotes a LOW finding; a check returns inconclusive and does not pass; metrics show reviewer precision | all above | tests/test_review_quality_acceptance.py |
| RQ-docs | README/help section, lesson recorded on noise controls | RQ-acceptance | docs/ddflow/, README.md |

Suggested build order: RQ-metrics (cheap, informs the rest) then RQ-note-* then RQ-confidence/RQ-profile, then RQ-check-*, then RQ-fix-*; the rest as capacity allows.

---------------------------------------------------------------------------

## What I could not verify
- CodeRabbit's internal verification/filtering of comments, models used, and any "code graph" feature (architecture page silent; UNVERIFIED).
- Walkthrough update behaviour on later commits (docs silent).
- Martian benchmark figures and the 270/70 developer tally: secondhand via search summaries; primary pages not opened (morphllm.com returned HTTP 429).
- Plan/pricing tier for each feature (pricing page not read).
- Whether ddflow's BL-structural phase overlaps structural (ast-grep) rules; not opened.
- Token and time costs above are my estimates, not measured.
