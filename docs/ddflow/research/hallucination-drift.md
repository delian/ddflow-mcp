# Hallucination and context drift: detecting it in a workflow and recovering from a smaller context

Task DX-drift-research (phase P-drift, ids HAL-*), agent res-drift, 2026-10-26. Operator request, verbatim, is recorded as session prompts; the follow-up on learning the threshold is section 10. Proposal state: `docs/ddflow/decisions/hallucination-drift.md`. Scripts: `docs/ddflow/research/scripts/drift_scan.py` (first detector, stdlib) and `drift_onset_sim.py` (synthetic convergence demo).

Evidence tags: **V** verified by me this session (raw fetch with curl, then parsed with `python3 -I`; the fetch was not summarised by a model unless said), **R** reported (a source says it, I did not reproduce), **U** unknown (looked, not found), **M** measured by me on this repo or this host, **O** operator observation (not independently measured).

## 1. Summary

1. **The operator's symptom is real in our own data.** A scan of the 107,146 events of this repo's log finds Russian words leaking into agent-written English in eight records dated 2026-10-24 21:05 to 2026-10-25 08:44 (**M**), for example a refutation probe containing a Cyrillic preposition and word where English belonged, and a coordinator gate note with Cyrillic letters glued to Latin words. Those records carry no served-model id and no context fill, so nobody can say which model, harness or fill wrote them. That is gap 1 (bug Bb54029aee9).
2. **A detector that costs nothing and needs no model exists for the language-leak half.** Unicode script analysis (stdlib `unicodedata`) over 3,388 tracked text files, 10,326 commit messages and 107,146 events: commit messages show zero foreign-script letters; the file tree shows 128 foreign letters in 11 files; 2 of those 11 files are real drift, 9 are legitimate fixtures or quoted examples. Naive "any non-ASCII" would flag at least 815 files that only contain arrows, box drawing and dashes (24%). The detector therefore needs script awareness, a mixed-word rule and an allowlist; with them the false-positive load is small and fixable (section 6).
3. **Language leak is the easy and late symptom; fabricated references, instruction loss and gate-failure drift are the hard and earlier ones.** The detector catalogue (section 5) puts them in cost order: structural and script checks (free, per commit), reference existence checks against the repo, ddflow records and the CLI parser (cheap, deterministic), behavioural counters from gate and tool events (free, noisy), canary probes at checkpoints (a few thousand tokens each, model-agnostic), and second-model or sampling-based checks only for high-risk output.
4. **Recovery is a ladder, not a switch** (section 8): note, warn, checkpoint, quarantine, rotate. A lane is rotated, never "repaired": ddflow closes it, writes a summary seed from ground truth (event log, git, gate state), validates the agent-authored note against that seed, starts a fresh lane with the seed, the brief and read access to the recording, and resumes from the last green commit. The lead cannot be closed by ddflow; for it ddflow produces the same pack and advises the operator or harness (compact with ddflow-written instructions, or a new session re-grounded by `brief`).
5. **Per-model effective context is a fact, not the advertised window.** The catalog needs `drift_onset_tokens` with source and confidence. The operator reports DeepSeek 3.x/V4 Pro degrading after about 350K (**O**); I found no vendor statement either way (**U**, section 4). Claude models on this host show no drift signal up to the 950K bucket (**M**), which is why a single global threshold is wrong in both directions.
6. **The threshold can be learned** (section 10): treat each (served model, harness) as a change-point problem over fill buckets, operate at a conservative lower bound minus a margin, lower automatically, raise only with evidence plus opt-in and bounded steps, and keep exploring with small canary lanes so the rotation does not hide what lies beyond it. A synthetic run (section 10.9) converges from a wrong prior in 5 epochs (prior too high) and stabilises at 300K when the true onset is 350K.
7. **Recommendation: option B** (section 12): deterministic detectors + context canaries + per-model thresholds + rotation with a ddflow-generated summary, learned thresholds in lowering-only form first. 19 queue items under phase P-drift (HAL-*); the first three (baseline scan, script detector, model facts) have minimal needs and can start now.

## 2. Method and limits

- Sources: raw HTML or markdown fetched with curl into a scratch directory and parsed with `python3 -I` (arXiv abstract pages, Hugging Face README files, Unicode UTS #39, lingua-py README, fastText page, Chroma report). Charts and figures were not extractable, so numbers read off figures are not quoted.
- Measurements: read-only; the scanner never prints scanned text, only counts and `U+XXXX` codepoints. Transcript scan covered `~/.claude/projects/*ddflow*`: 997 transcript files, 93,770 API calls, aggregates only.
- What I could not do: no DeepSeek transcripts exist in the Claude Code store on this host (all models there are Claude), so the per-model drift curve for DeepSeek is **U** until `B-am-ingest` / `DSH-session-ingest` reads dsh and lane recordings. No logprobs are used anywhere.
- Terminology: DRV-facts already has a "drift doctor" for fact version drift. This document says "hallucination drift" or "context drift" throughout, ids are HAL-*.

## 3. Taxonomy

What drift looks like in a coding workflow, grouped by what ddflow can observe. Each class names the detector family (section 5).

| Class | Example | Observable in | Detector |
|---|---|---|---|
| T1 Script/language leak | Cyrillic or CJK word in an English comment, commit message, doc, event note, identifier | text of diffs, commits, files, events, transcripts | D1-D4 |
| T2 Encoding damage | mojibake (`Ã©`), U+FFFD, zero-width or bidi controls, homoglyph identifiers | same | D2, D3 |
| T3 Degeneration | repetition loops, duplicated or truncated blocks, unbalanced syntax, nonsense identifiers, malformed JSON tool arguments | diffs, tool calls | D5-D7 |
| T4 Fabricated reference | path, symbol, flag, command, decision/task/bug id, package or API name that does not exist | diffs, reports, commands | D8-D11 |
| T5 Fabricated evidence | "tests pass" with no gate record; success claim contradicting gate state | reports vs gate events | D12 |
| T6 Instruction loss | Co-Authored-By trailer, push, pkill, edit outside globs, missing `--agent`, contradiction of a recorded decision | commands, commits, diffs vs rules/decisions | D13, D14 |
| T7 Behavioural drift | rising edit churn, reverts, gate failures per commit, review findings per diff size, tool-call error rate, step time | gate and tool events | D15-D17 |
| T8 Context-integrity loss | agent cannot restate its item, globs, governing decisions, last commits | canary answers vs ground truth | D18 |
| T9 Inconsistency under resampling | different answers to the same question | repeated sampling | D19 |

The literature agrees on the shape of T3-T5 for code: CodeHalu classes code hallucinations as mapping, naming, resource and logic (**V** abstract, arXiv 2405.00253); the thematic study of LLM-generated code reports 3 primary and 12 specific categories (**V** abstract, 2404.00971); CodeMirage defines a code-hallucination taxonomy and benchmark (**V** abstract, 2408.08333); package hallucination ("slopsquatting") is a systemic form of T4 (**V** abstract, 2406.10279: at least 5.2% of packages hallucinated for commercial and 21.7% for open-source models over 576,000 samples).

## 4. What the literature and vendors say

All dates are fetch dates 2026-10-26.

| Topic | Source | Finding | Tag |
|---|---|---|---|
| Position effects | Lost in the Middle, arXiv 2307.03172 (2023-07-06) | Performance is highest when relevant information is at the start or end and degrades in the middle, even for long-context models | V (abstract) |
| Effective vs claimed length | RULER, arXiv 2404.06654 (2024-04-09) | Near-perfect needle retrieval but large drops with length; of models claiming at least 32K only about half keep satisfactory performance at 32K | V (abstract) |
| Latent matching | NoLiMa, arXiv 2502.05167 (2025-02-07) | 13 models claiming at least 128K: at 32K, 11 fall below 50% of their short-context baseline; GPT-4o 99.3% to 69.7% | V (abstract) |
| Realistic long tasks | LongBench v2, arXiv 2412.15204 (2024-12-19) | 503 questions, contexts 8k to 2M words, best direct answer 50.1%, human experts 53.7% | V (abstract) |
| Length alone hurts | arXiv 2510.05381 (2025-10-06) | Performance falls 13.9%-85% as input grows even with perfect retrieval, even with masked or whitespace filler; reciting the evidence first mitigates | V (abstract) |
| Non-uniform use of context | Chroma "Context Rot" (2025-07-14), 18 LLMs incl. Claude 4, GPT-4.1, Gemini 2.5, Qwen3 | Performance becomes increasingly unreliable as input grows, even on simple tasks; the page's figures were not extractable | V (page text, figures unread) |
| Language confusion | arXiv 2406.20052 (2024-06-28), Language Confusion Benchmark, 15 languages | Even strong models fail to answer consistently in the requested language; worse for base and English-centric instruct models, complex prompts, high temperature; mitigated by few-shot, multilingual SFT, preference tuning | V (abstract) |
| Sampling-based detection | SelfCheckGPT, arXiv 2303.08896 (2023-03-15) | Black-box: sampled answers agree when the model knows, diverge when it hallucinates | V (abstract) |
| Semantic uncertainty | Kuhn et al., arXiv 2302.09664 (2023-02-19) | Semantic entropy over meaning-equivalent clusters predicts QA correctness better than token-level baselines; unsupervised, one model | V (abstract) |
| Hidden characters | Trojan Source, arXiv 2111.00169 (2021-10-30) | Bidi and invisible Unicode make code appear different to humans and compilers; defenses at editor, repository and build pipeline | V (abstract) |
| Unicode security | UTS #39 v18.0.0 (2026-08-27) | Confusable skeleton comparison, mixed-script detection, restriction levels (single script, highly/moderately restrictive); Moderately Restrictive allows Latin plus one recommended script except Cyrillic and Greek | V (page text) |
| Language ID | lingua-py README; fastText lid.176; CLD3 README | lingua: 75 languages, accuracy falls on short text, low-accuracy mode drops accuracy under 120 characters, can split mixed-language text; fastText: 176 languages, 126 MB or 917 kB models; CLD3: neural n-gram model | V (READMEs, vendor claims) |
| DeepSeek-V3.2 | arXiv 2512.02556 (2025-12-02), HF card | DeepSeek Sparse Attention for long-context efficiency "while preserving model performance"; no per-length quality curve in the card | V |
| DeepSeek-V4 (Pro/Flash) | HF card `deepseek-ai/DeepSeek-V4-Pro` (preview) | 1M context; hybrid compressed attention (27% FLOPs, 10% KV cache vs V3.2 at 1M); MRCR 1M 83.5 and CorpusQA 1M 62.0 for V4-Pro Max versus 92.9/71.7 for Opus-4.6 Max; for Think Max "set the context window to at least 384K"; **no statement about agentic quality beyond 128K, language mixing or known issues** | V (raw card) |
| The 350K onset | operator | DeepSeek 3.x Pro degrades after about 350K with Russian and other words leaking into code and docs | O |

Reading: (a) every long-context study finds an effective length well below the advertised one and finds it model-specific; (b) the language-confusion literature is about response language, not about English output that is poisoned mid-stream, so the leak we see is closer to degeneration than to the benchmark; the benchmark still tells us that high sampling temperature and complex prompts raise it; (c) retrieval benchmarks (MRCR, NIAH) do not measure agentic coding quality, so a vendor 1M retrieval score says nothing about the onset we care about; this is why we must measure our own curve (sections 9-10). Nothing found says DeepSeek 3.x/V4 has a documented drift onset: **U**.

## 5. Detector catalogue

Costs: tokens (agent context), CPU, and a rough false-positive (FP) note. "Free" means no model call. All run on text ddflow already has.

| Id | Signal | Where it runs | Cost | FP notes and controls |
|---|---|---|---|---|
| D1 | Foreign-script letters: letters whose Unicode script (first word of `unicodedata.name`) is outside the project allowlist | commit-msg and pre-commit hooks on added lines; hygiene gate on the diff; event writer for agent-authored text | free, about 5 ms per file; whole tree 17 s in Python | legitimate: i18n fixtures, tests, quoted examples, names, math symbols. Controls: per-project allowed scripts, path allowlist (`tests/**`, fixtures), skip text inside backticks/quotes when the same token appears in the surrounding code, severity by context (identifier > comment > doc > string > fixture path), accepted records (D-lean-review) |
| D2 | Mixed-script word: one `\w+` token with Latin plus another script | same | free | high precision for homoglyph and glued leaks (`зmelding`); FP: math such as Greek with Latin subscripts. Exclude a configured math allowlist |
| D3 | Invisible and structural: zero-width (U+200B-200F, 2060-2064, FEFF), bidi controls (202A-202E, 2066-2069), U+FFFD, private use, C0/C1 controls, mojibake patterns (`Ã.`, `â€.`, `Ð.`) | same; always on | free | FP: ANSI ESC in captured gate output (54 in events), powerline glyph in prompts (18), documented mojibake examples (bug text). Controls: exempt captured output fields, require context |
| D4 | Language ID (lingua, fastText, CLD3) for prose in comments, docs, commit bodies | optional second stage, only on text D1/D2 already flagged or on long prose | CPU, model files 1 MB-126 MB, optional dependency | unreliable below about 120 characters (lingua README); unnecessary when script analysis answers; **not recommended in the first versions**. Needed only for same-script leaks (a German or Polish sentence in Latin script), which D1 cannot see |
| D5 | Repetition: n-gram repeat ratio, duplicated adjacent blocks, compression ratio (zlib) of added text | hygiene gate | free | FP: generated tables, lockfiles; exempt by path |
| D6 | Syntax and format: unbalanced brackets, parse failure of changed `.py/.json/.toml/.md` fences, ruff/mypy on changed files | existing standards gate and ci; precommit | existing cost | already enforced; add as a drift signal source (a syntax break at high fill is a drift hit) |
| D7 | Malformed tool calls: JSON/argument errors, unknown tool names, wrong-arity calls, refusal-by-schema counts | MCP server and CLI parser already see them; count per lane per fill bucket | free | FP: harness bugs, schema changes. Keep a per-bucket rate, not single events |
| D8 | Path references: `docs/...`, `ddflow/...`, `tests/...` named in a diff, commit message or report that do not exist in the tree or in the diff | hygiene gate, report check | free, `git ls-files` set | FP: files about to be created in the same diff; planned paths in docs (mark with "(planned)"). Use diff-aware set |
| D9 | ddflow id references: task, bug, decision, rule, lesson, research ids that do not exist in the log | commit-msg hook, report check, event writer | free (state lookup) | FP: ids in prose examples; require the id pattern plus a verb context or a link syntax |
| D10 | Command and flag references: `ddflow <cmd> --flag` in text or tool calls not accepted by the real parser | tool-call check (PreToolUse where available), doc check | free, parser introspection | FP: examples of removed flags; allow history sections. A rejected command is already an error, so this adds docs and commit text |
| D11 | Symbol and package references: function/class names asserted in prose or tests that do not exist (`ast` index of the tree), imports of packages not installed or not in lockfile | standards gate | free for symbols; package index lookup is a network call (offline: installed set only) | FP: new symbols defined in the same diff; dynamic names. Slopsquatting: refuse a new dependency unless present in the lockfile or approved |
| D12 | Evidence mismatch: report says "tests pass" / "gate passed" but no matching gate event with tree identity; counts in reports differ from gate evidence | complete/merge check, digest | free | none known; compare tree_sha |
| D13 | Rule violations: Co-Authored-By trailer, push, pkill, edits outside lease globs, missing `--agent`, hooks disabled | commit-msg and pre-commit hooks (exist), SW-git-fallback lease hook, PreToolUse | free | already refused where hooks exist; count refusals per lane per bucket as a signal |
| D14 | Decision contradiction: diff touches globs governed by an accepted decision and violates a checkable clause | review prompt context, standards | needs a machine-checkable clause; mostly review | high FP if free-text; only for decisions with a check |
| D15 | Edit churn: re-edits of the same file, reverts, amend counts per commit | git and tool events | free | confounded by task difficulty; in my transcript sample re-edit rate is cumulative per session and rises with position even for models showing no other drift, so use only within-task deltas |
| D16 | Gate failures per commit and review findings per KLOC, by lane and fill bucket | gate events | free | confounded by diff size and task kind; normalise by diff lines and kind (ledger SW-ledger) |
| D17 | Tool-call error rate and step time per fill bucket | transcripts, tool events | free | measured: error rate falls as fill grows on Claude (4.3% in 50K bucket to 1.0% at 400K) because long sessions are read-heavy; a rising rate within the same task is the signal, not the raw level |
| D18 | Canary: ask the agent, at a checkpoint, to restate item id and title, its globs, three governing decisions (ids), and the last three commits; score against ground truth | UserPromptSubmit/PostToolUse hook injection, or at a heartbeat; harness-dependent | about 600-1,500 tokens per probe (question 150, answer 200-500), cache-friendly if placed at the end | FP: legitimate compaction before the probe; probe must be answered from context only (no tool allowed). Score = fraction of items correct; threshold learned per model (section 10) |
| D19 | Resampling consistency (SelfCheckGPT style) | only high-risk outputs: a decision text, a final report, a migration | N times a short generation; expensive | not for routine use. Needs sampling control the harness may not expose; needs a second model or temperature |
| D20 | Cross-family reviewer | exists as the critic gate (`rubber_duck`, `critic` on a different model family) | existing | reviewers read drifted text as authoritative; do not rely on it alone, and a reviewer at high fill can drift itself |

Detectors D1, D3, D5, D8, D9, D12, D13 are the "hygiene" set: free, deterministic, runnable at commit time without a model.

## 6. Measured baseline (this repo, 2026-10-26)

Script: `python3 -I docs/ddflow/research/scripts/drift_scan.py {files|commits|events|transcripts}`; output is counts and codepoints only. Scan of the worktree at main `63dfd548b`.

**Tracked files** (3,388 text files, 1 binary skipped; 17 s):

| Category | Count | Where |
|---|---|---|
| Foreign letters | 128 in 11 files: Cyrillic 53, Greek 33, CJK 32, Hangul 5, Katakana 3, Bengali 2 | tests fixtures (`test_search_porter`, `test_textkit*`, `test_toml_string_writer`), `core/textsim.py` (a fold table), event shards |
| Mixed-script words | 8 | event shards (5 Cyrillic+Latin, 3 Greek+Latin math) |
| Replacement U+FFFD | 8 | tests and event shards quoting the mojibake bug |
| Mojibake pattern | 3 | event shards quoting `café` bug reports |
| Zero-width | 1 | `core/docchunk.py` string constant |
| Private use | 18 | powerline glyph U+E0A0 in recorded prompts |
| Non-ASCII symbols only (arrows, box drawing, dashes, math) | 37,678 characters in 815 files | docs, README, data |
| bidi | 0 | |

By file type: Python comments 0 foreign letters; Python strings 63 (all test fixtures); docs 0; data (event shards and json) 65.

**Commit messages**: 10,326 commits; 0 foreign letters, 0 invisible, 0 mojibake; 383 non-ASCII symbols in 71 commits. **Zero-FP on a script rule for commit messages at present**; the 71 are dashes and arrows.

**Event log** (107,146 events, 0 unparsable): 65 foreign letters in 6 shards, 8 mixed-script words, 55 control characters (54 ESC in captured gate output, 1 NUL), 18 private-use, 6 U+FFFD. Real drift: the Cyrillic words in `review.triaged` probe text, `gate.passed` `evidence.note` and refutation probes of two agents (readme-decide-worker, Monster3 coordinator), 2026-10-24 21:05 to 2026-10-25 08:44. Legitimate: a CJK filename example in batchC reviewer findings, Greek math in U1/U11/bugfixM, mojibake examples in U4 and pa-autocapture.

**False-positive rates of candidate rules** (adjudicated by me by reading the surrounding text of each hit; counts are files or records):

| Rule | Flagged | True drift | Precision |
|---|---|---|---|
| "any non-ASCII character" | at least 815 files | 2 files | under 0.3% |
| "any non-Latin letter" | 11 files | 2 | 18% |
| non-Latin letter outside `tests/**` and `core/textsim.py` | 6 files (all event shards) | 2 | 33% |
| mixed-script word (Latin plus Cyrillic) | 2 files | 2 | 100% (n small) |
| mixed-script word incl. Greek | 3 files | 2 | 67% (math FP) |
| foreign letters in commit messages | 0 | 0 | n/a |

Interpretation: the useful pair is **mixed-word (Cyrillic or other non-Latin plus Latin) + foreign letters outside allowlisted fixture paths**, with an exception for letters inside backticks or code spans and for a Greek/math allowlist. Expected residual FP on this repo: a few shard records per quarter, handled once with an accepted record each.

**Transcripts** (997 Claude Code transcript files for this project, 93,770 API calls, 96,731 tool calls; models: claude-sonnet-5-5 68,355 calls, claude-opus-5-5 22,860, others under 1,500 each):

| Signal | Result |
|---|---|
| Foreign letters in assistant text | 2 characters in 2 sessions with no operator basis; 0 mixed words, 0 replacement, 0 zero-width, 0 bidi, 0 mojibake in 6.13M characters of assistant text |
| Foreign letters in tool-call inputs | 45 (fixtures and test strings), 9 mojibake patterns, 3 replacement chars |
| Tool-call error rate by fill bucket (Sonnet 5.5) | 4.3% (50K), 3.6% (100K), 2.3% (150K), 1.9% (200K), 1.5% (250K), 1.0-1.7% (300K-600K), under 1.5% above |
| Opus 5.5 | 6.0% (0-50K), 2.2% (100K), 1.6% (200K), 1.1-1.9% (400K-600K) |
| Re-edit share | 3% at 50K rising to 16% at 200K for Sonnet; Opus 20-43% below 200K and lower above; confounded by session position |

Conclusions: (1) on Claude through Claude Code, none of the free text detectors fires up to the 950K bucket, so Claude is the clean control and the detectors do not cry wolf on it; (2) the tool-error and re-edit curves are dominated by confounds (session phase, task mix), which is why the learner in section 10 compares rates within matched buckets and treats raw levels as uninformative; (3) the DeepSeek curve is **U** here: lanes of other harnesses are not in this store (`DSH-session-ingest`, `B-am-ingest`).

## 7. Context-based detection

**Per-model effective context** is a measured property: for each (served model, harness) the fill at which the signals above begin to rise. Section 9 puts it in the catalog; section 10 learns it.

**Canary design (D18, HAL-canary).** At checkpoints (every N tokens of fill, at a task boundary, after compaction, and at the 150K/250K/350K ladder points of D-CC-Q4) ddflow injects a fixed-size probe through a hook door or the lane prompt: "without using tools, state (1) your item id and title, (2) your globs, (3) the ids of the decisions governing your files, (4) the last three commit subjects you made." ddflow already knows each answer from the log and git. The score is the fraction of exact id matches plus a bounded fuzzy match on titles; free-text is compared with `core/textsim.py` and never trusted as instructions. Properties: model-agnostic, no logprobs, about 1K tokens, scoring is deterministic, and the score is data for the ladder and the learner. Limits: a lane that just compacted may fail honestly (re-ground, then re-ask once); an agent can pass the canary and still hallucinate in code (a canary measures context integrity, not correctness); a canary placed mid-task spends cache. Harness reach: UserPromptSubmit/PostToolUse hook doors exist on Claude Code; where a harness has none (see `agent-token-features-pass2.md`), the lane prompt carries the probe at a safe point, or the canary is unavailable and recorded so.

**Resampling (D19)** is kept for outputs where one wrong word is expensive (decision text, a final report that will be quoted): ask twice at different temperature or from a second model and compare ids and claims; disagreement files a finding. Not a routine check; cost multiples of the output.

## 8. Recovery workflow

### 8.1 Policy ladder

| Level | Trigger (any, configurable; thresholds per model, section 9) | Action |
|---|---|---|
| 0 note | one low-severity hit (accepted pattern excluded) | record `drift.hit` event with evidence; no nudge |
| 1 warn | two hits in a window, or a canary score under 0.8, or fill over the model's `warn` mark | nudge the agent once ("your last output contained X; re-read the brief"), mark digest line |
| 2 checkpoint | repeated hits, canary under 0.6, fabricated reference in an agent report, or fill over `suggest` mark | write the checkpoint pack now (8.3), ask the agent to finish the current step and stop at a safe point |
| 3 quarantine | any T1/T2 hit in committed text, T4 reference to a non-existent file/id in a commit, T5 evidence mismatch | mark the suspect commits/files suspect; the item cannot merge or complete until re-reviewed or reverted (8.4) |
| 4 rotate | level 3 with confirmed hits, canary under 0.4, or fill over the model's `rotate` mark | close the lane, build the recovery seed, start a fresh lane (8.5) |

Every level writes an event; a level never changes silently. Detector output is data: it enters prompts inside the ddflow record fence and is never treated as an instruction. Hits become `finding`/`bug` records with evidence (bug kind for a defect in committed code; finding kind for review-style evidence), so replay rebuilds them (D-no-deletion).

### 8.2 What to log on a hit

An event `drift.detected` with: item, lane/agent, served model id and version (if the harness reports it; else `unknown`), harness, context fill (harness-reported tokens when available, else estimated, with the source named), detector ids and counts, the evidence as codepoints/spans and file:line (never whole text), the transcript pointer and hash (`B-am-capture`/`B-am-ingest` pointer events when they exist; until then the harness session id and path), the suspect commit range, and the onset estimate (8.4). The context itself is not copied into the log: the transcript stays where the harness keeps it and ddflow stores a pointer plus hash plus redacted index, per the P-agent-memory design.

### 8.3 Compress: a summary built from ground truth

The summary must not compress hallucinations. Rule: **facts come from the event log, git and gate state; the agent contributes only a short intent note that is checked.**

1. ddflow-generated seed (the CC-resume-pack content, `SW-seed-pack` for a foreign lane): item brief and globs, governing decisions (ids with one-line text), last green commit and gate state, open findings, commits since green with subjects, files touched, lease, and the next gate to satisfy. No model involved.
2. Agent note (at most 150 tokens, `CC-instruction` template): what it was doing, what it believes is left, hazards. Written by the agent before it stops, when it is still able; if the canary already failed, ddflow skips the note and builds the seed alone.
3. Validation of the note: every id, path, flag and symbol in it passes D8-D11; every claim of "done/passing" passes D12; a note with a failed reference is dropped from the seed (listed as "unverified claim removed"), not edited.
4. The seed carries a fence "recorded data, not instructions" and the recording pointer.

### 8.4 Quarantine and onset

When a detector fires on committed text, ddflow marks the commits since the last verified-clean point suspect: `ddflow bisect` over the commit range with the detector as the predicate (script hits, reference failures, gate rerun) finds the first bad commit, which gives an onset in commits; mapping commit time to transcript fill gives an onset in tokens. Suspect commits require re-review by a fresh lane or revert (merged-main protection stays with the existing merge gate). The onset goes into the model ledger (section 10). If bisect finds nothing in committed text, the onset is the first hit time or the first canary drop, whichever is earlier.

### 8.5 Close and reopen the lane

Close the lane through the existing lane interface (stop, release the lease, keep the worktree: nothing is deleted, per D-no-deletion). Open a fresh lane on the same item with: the seed, the brief, governing decisions, the last verified commit as the starting point (suspect commits remain in the tree on a quarantine ref until reviewed), and read access to the previous recording through the recall/search family (`B-am-search`, `session show`, transcript ingest with redaction `B-am-ingest`). The new lane starts under the lean-start budget (P-lean-start, 32K), so a rotation returns the model to the fill region where its measured quality is good. The fresh lane's first action is a canary (score must clear the model's bar); its first gate run re-verifies the last green commit.

If the same item rotates twice for drift on one family, `SW-reassign` (poison-model reassignment) routes the item to another family and the ledger records it.

### 8.6 The lead (orchestrator)

ddflow cannot close the lead. It can: detect (the lead's own text appears in ddflow records and commit messages; canary at checkpoints through UserPromptSubmit), advise, and make restart cheap.

1. Advise: the CC-advisor level reaches the operator through the digest (`CC-digest`) and, where hooks allow, a once-per-level nudge into the lead (`CC-hooks`). The advice names an exact action: "compact now with these instructions", or "start a new session; run `ddflow brief` and `ddflow handoff`".
2. Compact with ddflow-generated instructions (`CC-instruction`): the seed text (fleet registry, open asks, directives, decisions) is what the harness is told to preserve; after compaction the PostCompact/SessionStart door re-injects the resume pack (`CC-resume-pack`, `CC-hooks`).
3. New session: `DX-handoff-pack` rebuilds directives, agent reports, schedules, fleet and open asks, so a fresh lead re-grounds from `brief`, `digest` and the fleet registry (`DX-fleet-registry`) rather than from memory.
4. Because a drifted lead may write the handoff badly, handoff content is derived from the log, with the lead's note validated as in 8.3.

Per-client facts (compaction window knobs, hooks, resume) are in `agent-token-features.md`, `agent-token-features-pass2.md` and `compact-window-per-agent.md`; the lead action depends on them: Claude Code can set `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (100K-1M) and subagent `autoCompactWindow`, which lets ddflow put the window itself at or below the drift onset where the harness allows; Copilot and Cursor have no launch-time threshold, so for them the lane is rotated by ddflow rather than compacted by the harness.

## 9. Per-model policy

**Catalog fact** `drift_onset_tokens` (extends `DRV-model-catalog`/`CC-inventory` records, same provenance and expiry as `DRV-facts`):

```
model_id (served, versioned) · harness · advertised_window · effective_context
drift_onset_tokens · source = documented | learned | operator | default
confidence = low | medium | high · samples · window (dates) · updated · expires
```

Rules:

- `effective_context` is distinct from the advertised window. The operating marks derive from the onset: `warn = onset - 100K`, `suggest = onset - 50K`, `rotate = onset - margin`, never above the existing D-CC-Q4 table (lanes 150/250/350K, lead 250/400K, unknown family 150/250K) and never above TE-context-caps 250K lane rotation unless the operator raised it. So the table is the ceiling; the learned onset can only pull marks lower.
- **Unknown model**: use the "unknown family" row (150/250K) until facts exist; D-session-start-budget limits the always-loaded context independently.
- **Known problem**: a model with a recorded drift problem (`confidence` medium or higher, onset under the lane default) is routed away for long items: `D-model-routing` safe-point switching and `SW-reassign`; long items go to families with higher measured onset. Routing weight, not a ban.
- **Layering**: defaults in the shipped catalog; project config may lower; company floors tighten-only (D-org-config, D-company-workflows): a company may require a lower rotation mark or demand canaries, a project cannot raise above the company ceiling.
- **Ledger**: a quality ledger per (family, model, harness, task kind, fill bucket) with first-pass gate rate, findings per diff size, detector hits, fabricated-reference rate, canary scores (extends `SW-ledger`, fed by `HAL-measure`). `SW-Q10`'s exploration share (10% for pairs under 5 samples) is the same mechanism as section 10.5.

Seed values (all **default**, replaced by facts): DeepSeek family lanes `rotate` 300K (below the operator's reported 350K, margin 50K); Claude and unknown families per D-CC-Q4. The 350K figure is **O** until measured by section 10.

## 10. Adaptive learning of the per-model threshold

Question (operator): can ddflow discover, log and learn the healthy hallucination threshold per model so it adjusts even when it is not known? Yes, in a conservative form: estimate, act on a lower bound, only ever lower automatically, and keep a small, deliberate exploration budget so the system can notice that a model is better than assumed.

### 10.1 Estimation as change-point detection

Unit: per (served model id+version, harness, task kind class) key. Observation unit: one "step" (an API call or a fixed 5K-token slice of a lane's context growth) with a fill bucket (50K wide) and a binary or small-integer outcome from the signals: detector hit (D1-D3, D5), fabricated reference (D8-D11), gate failure on the commit made in that step, tool-call error, review finding raised against it, canary score below the bar. Combine signals into one **hit** per step with a configured weighting (default: any D1/D2/D3/D8-D11/D12 hit or a canary failure counts; gate failure and tool error count only in the within-task comparison).

Estimator (HAL-learn-onset):

1. Reference rate r0 = pooled hit rate for fill under 150K (the region assumed healthy), with a Wilson upper bound.
2. For each bucket b with at least `MIN_N` steps (default 150), compute the Wilson lower bound of its hit rate. The bucket is **elevated** if the lower bound exceeds r0's upper bound.
3. Onset = start of the first elevated bucket whose successor is also elevated or not yet observed (persistence rule, kills one-bucket noise).
4. Operating threshold = onset minus margin (default 50K, one bucket), floored at a catalog minimum.

This is a one-sided sequential test, not a full Bayesian change-point model; a Bayesian online change-point (Adams-MacKay) or a hazard model by bucket is an optional upgrade once there is enough data. The simple rule is explainable in a doctor line and cheap enough to rerun at each gate event.

**Priors**: start from the catalog's documented or default fact (section 9). The prior sets the starting threshold, not the estimate: with no data the operating threshold is the prior; evidence only moves it down (10.4). Sample-size minimum before any change: `MIN_N` per bucket and at least `K` distinct lanes (default 3) contributing to the bucket, so one bad lane (a hard task) cannot lower a model's threshold. Below that the fact says `confidence=low`.

### 10.2 Confounds and controls

| Confound | Control |
|---|---|
| Task difficulty correlates with long contexts (hard tasks run longer) | stratify by task kind and diff size; compare matched pairs where available; require several lanes (`K`); use detectors independent of difficulty (script leak, fabricated reference, canary) as the primary signal and gate failures only as supporting |
| Tool outputs inflate fill with benign text | bucket on total fill but also record the share of tool-result tokens; a canary at a fixed position in a fixed-size synthetic context removes content effects (10.5) |
| Harness-reported vs estimated tokens | store `fill_source` (reported/estimated) with each observation; learn on reported only unless the harness cannot report, then widen the margin; never mix sources in one bucket |
| Model or version silently changes (alias moved, provider rerouting) | key by the served model id and version string from the response; hash the id set seen; if the served id changes mid-key, start a new key; also run a cheap change detector on the low-fill region (r0 shifts significantly) and mark the old estimate `stale` |
| Old estimates stay forever | expiry (default 90 days) and decay: samples older than the window carry half weight; an expired fact reverts to its prior with `confidence=low` |
| Reasoning mode vs non-thinking (V4 cards show different behaviour per mode, and Think Max wants 384K+) | include reasoning mode/effort in the key when the harness reports it |
| Compaction resets fill without resetting the model | fill is the current context size, not tokens spent; observations after compaction carry the post-compaction fill |
| Detector gaps (same-script leaks, subtle logic errors) | the learner knows only what the signals see; report which signal classes were active (a threshold learned from script leaks alone is `partial`) |

### 10.3 The feedback loop

If every lane rotates at the threshold T, no data exists beyond T, so the estimate can never rise and a too-conservative T is permanent. Policy:

- **Default (always on): lowering needs no exploration**, because lanes see up to T and a problem below T is observed.
- **Canary probes beyond T, in cheap sandbox lanes** (HAL-explore): a fixed-size synthetic probe lane that fills a context to a chosen level with deterministic filler plus a sample of real project text (read-only, no repository write), then asks the canary questions and a small set of checkable tasks (copy this id list, restate these decisions, write a comment in English about this function). It costs the fill tokens once per probe, run at off-peak, capped by a monthly token budget, never on a lane that holds an item. Pure canary measurement avoids risking real work but measures integrity under synthetic filler, not agentic coding; it detects script leaks and recall failures well and is the first choice.
- **Exploration share on real lanes** (relates `SW-Q10`, the quality ledger): a small share (default 10-15%) of low-risk items (docs, mechanical fixes) is allowed to run to T plus one bucket (50K) with D1-D3, D8-D12 and a canary every 25K enforced and the item flagged for an extra review. Risky items never explore. This is opt-in per project; default off in the first release.
- **Raising the threshold** is allowed only if all hold: the project enabled `[drift].allow_raise`; the bucket above T has at least `MIN_N` steps over at least `K` lanes with a Wilson upper bound within a tolerance of r0; the step is bounded (default +25K per epoch, at most +100K above the shipped fact); the company ceiling (tighten-only) is respected. Otherwise a proposed raise is only reported to the operator in the digest.

### 10.4 Guard rails

1. Automatic changes only **lower** the operating threshold.
2. A lowered value is never silently hidden: it appears in `ddflow doctor` and the digest with the evidence ("rotate for model X lowered 350K to 300K: 4/180 steps hit at 350-400K vs 1.1% below 150K, 5 lanes").
3. Raising needs evidence plus opt-in, bounded steps.
4. Company floors and ceilings: tighten-only; a project cannot raise above the company ceiling nor lower below a company floor that exists to bound cost.
5. A lowering is reversible by an operator record (`source=operator` wins over `learned` until it expires or the operator removes it).
6. Rate limit: at most one automatic change per key per epoch (default 24 hours) to avoid oscillation.

### 10.5 What is recorded

`model_fact` (event kind `model.fact` appended, never edited in place, so `ddflow replay` rebuilds it):

```
key = (served_model, version, harness, mode)
fact = drift_onset_tokens
value, operating_threshold, confidence (low|medium|high), samples (steps), lanes, window (from, to),
last_update, source = documented | learned | operator | default,
signals_used = [script, refs, canary, gates ...], partial = true|false, fill_source
evidence = pointers to ledger rows and bucket counters (aggregates, no text)
```

Shown in: `ddflow doctor` (a line per key whose operating threshold differs from the shipped fact, and any stale or low-confidence fact), the operator digest (changes since last digest), `ddflow model show` / the model catalog view (`DRV-model-catalog`, `HAL-model-facts`), and the compaction advisor input (`CC-advisor`). Observations are kept as counters per (key, bucket) derived from gate and detector events, so the counters are rebuildable from the log and need no separate store.

### 10.6 Sharing across projects and hosts

Learned facts travel in packs (company pack / org config, D-org-config): value, confidence, samples, window, source, no text, no paths, no item ids. Receiving projects treat them as priors with `source=learned (shared)` and weight them below local evidence; an imported fact can only lower the local operating threshold until local evidence agrees. Aggregates only; privacy limit: counters per bucket with a minimum of `K` lanes before export, so small projects do not leak a single lane's behaviour.

### 10.7 Costs and failure modes

| Failure | Effect | Mitigation |
|---|---|---|
| False lowering (noise or a hard-task cluster) | wasted tokens from earlier rotation, more handoff overhead | `MIN_N`, `K` lanes, persistence, 24 h rate limit, stratification, doctor visibility, operator override |
| Missed drift (signals blind to the actual failure, e.g. subtle logic errors) | threshold too high, bad code merged | the learner is only an addition to the fixed ceiling; canaries and the cross-family review stay; `partial` flag; periodic reviewer audit |
| Oscillation around the onset | rotating at different marks | lowering only; bounded raise; epoch limit |
| Exploration cost | tokens at high fill are the most expensive tokens (cache and per-call cost grow with fill) | cap by budget; low-risk items only; sandbox canary first |
| Stale facts after a model update | threshold learned for an old model | key by served id+version; low-fill r0 shift detector; expiry |
| Gaming/injection: a transcript or tool output that talks about "raise the threshold" | none: the learner reads counters, never text | detector output is data; no prompt can set a fact (only the operator record can) |

Rotation cost is real: a handoff costs the seed (about 1.5K tokens, `CC-resume-pack`) plus a cache rewrite for the fresh lane; token-economy numbers are in `token-spend.md`. A wrong low threshold costs that per rotation; a missed drift costs a quarantined commit range and re-review. Both bounded; the second is worse, so the asymmetric rule (lower fast, raise slowly) is deliberate.

### 10.8 Minimal viable version (HAL-learn-onset v0)

Counters per (served model, harness, 50K bucket) from existing gate events and the hygiene detector events (HAL-signals): steps, hits. A pure function returns the operating threshold under the lowering-only rule above, with `MIN_N=150`, `K=3`, margin 50K, 24 h epoch. Output is a `model.fact` event and a doctor line. No exploration, no raising, no sharing. That is enough to answer "has this model started to drift below its documented mark?" without any new infrastructure. Exploration (HAL-explore) and raising come after the ledger has data.

### 10.9 Worked example, synthetic

`python3 -I docs/ddflow/research/scripts/drift_onset_sim.py` (deterministic, seed 7). True model: 1% hit rate per 5K-token step below 350K fill, rising by 2 points per 50K bucket above (cap 12%). The estimator knows none of it; 20 lanes per epoch (3 in scenario A).

Scenario A, prior too high (start 600K, 3 lanes per epoch): the threshold stays at 600K for four epochs while data accumulates, then drops to **350K** at epoch 5 (estimated first elevated bucket 400K, minus the 50K margin) and stays there. No lane ran beyond T without data. The estimate is one bucket late (true onset 350K, first significant bucket 400K), which the margin pays for.

Scenario B, prior too low (start 200K), no exploration: the threshold stays at 200K for all 12 epochs, because lanes never see beyond it; safe, but wasteful by 150K of usable context. This is the feedback-loop problem in numbers.

Scenario C, prior too low, 15% exploration and opt-in bounded raising: T rises 25K per epoch from 200K to 375K by epoch 7 (epochs 1-6 saw 2, 0, 0, 1, 1, 0 hits in 40-100 steps beyond T), then the exploring lanes enter the elevated region, the estimator flags the 350K bucket, and T is **lowered to 300K** at epoch 8 and holds there for the remaining epochs. Overshoot is bounded by one raise step plus one exploration bucket, and the margin leaves the final operating point 50K under the true onset. The synthetic data validates only the mechanics (rule behaviour under a known truth); real signals will be noisier, which is why the minimums exist.

## 11. Integration points in ddflow

| Where | What runs | Facts and cost |
|---|---|---|
| commit-msg hook (`ddflow hooks check-msg`, exists) | D1-D3 on the message; D9 on ids; D13 already | per commit, under 50 ms; hook installed by `hooks install` |
| pre-commit hook (`ddflow hooks check-commit`, exists) | D1-D3, D5 on added lines only; D8 on named paths | per commit, scale with diff |
| `hygiene` check inside the `standards` gate, or a new gate | full diff: D1-D3, D5, D8-D12; produces gate evidence with counts; severity gates blocking by class | a new gate adds a pipeline column to every kind; start as a standards sub-result (as `RQ-toolctx-secrets` does for secret scan), promote later |
| PostToolUse/UserPromptSubmit (where the harness has them) | D7 per tool call counters; D18 canary injection; fill reading | harness reach per `agent-token-features-pass2.md`; unavailable is recorded, not passed |
| event writer | D1-D3 on agent-authored text fields (`probe`, `note`, `reason`) before append; a hit appends a `drift.detected` event and still appends the record (nothing dropped, D-no-deletion) | cheap; protects the log from silently carrying leaks |
| review prompt context | detector summary as data in the reviewer prompt for the diff under review; reviewer told which spans were flagged | tokens: a few hundred |
| `ddflow doctor` | learned/stale facts, hit counters per model, detector installed and its last run | free |
| digest/operator queue | `HAL-digest`: drift events by level, lanes rotated, suspect commits awaiting review, threshold changes | ties to `CC-digest`, `SW-digest` |
| `ddflow bisect` | onset search over a suspect commit range with a detector predicate | exists |
| accept/waive | `accepted` record per D-lean-review: a pattern (file glob + codepoint class + reason) accepted once never re-flags; severity-gated blocking | the FP control |

False-positive controls in short: per-project allowed scripts, path allowlists, context-aware severity, quoted-text exemption, baseline file (`ddflow drift baseline` records current accepted hits so only new ones fire), accepted records with a reason, counts per key rather than single events for behavioural signals.

Trust: detector output is data in every prompt (fenced record), never instructions; the scanner reads text, never executes it; scanned text from transcripts or third parties is untrusted; the scanner prints codepoints and counts, not content.

## 12. Options and recommendation

| Option | What | Cost | Misses |
|---|---|---|---|
| A prompt-only | tell agents to keep English, not to exceed N tokens, to verify references | zero code | the drifted agent is the one reading the instruction; the failure we saw happens precisely when instructions are forgotten |
| **B deterministic + canary + per-model thresholds + rotation with ground-truth seed (recommended)** | hygiene detectors D1-D3, D5, D8-D13; canary D18; behavioural counters; catalog `drift_onset`; ladder; quarantine; seed from the log; lane rotation; lead advice; learned threshold in lowering-only form | 19 small tasks, almost all pure functions over data ddflow already holds | same-script semantic drift (logic errors with clean text) is left to gates and review |
| C B + second-model verification and statistical self-consistency | D19, a cheap second model reading flagged diffs, semantic entropy on reports | tokens, a dependency on a second family, sampling control | still needs B to decide when to ask |

Recommend **B**, with C's second-model read limited to diffs that D-detectors flagged. Reasons: the observed failure is cheaply visible in text (8 records in 107K events) and in references, the detectors are free and deterministic, thresholds are per-model facts rather than prose rules, and recovery reuses seed/handoff work already queued (CC-resume-pack, DX-handoff-pack, SW-seed-pack, B-am-ingest).

**What would invalidate it**: (1) measurement on DeepSeek transcripts showing drift mostly as same-script semantic errors that script and reference checks miss (then C weighs more and the canary is the main signal); (2) a canary that models pass while drifting (then canary scoring needs harder probes or is dropped); (3) rotation costing more than the rework drift causes at the learned thresholds (check with `HAL-measure`: rotations versus quarantined commits); (4) harnesses that expose neither fill nor served model id, leaving only text detectors.

**Rollout** is the HAL- queue (section 13): wave 1 gives the detector and facts with no behaviour change; wave 2 adds signals, references and canary; wave 3 hooks, gate, ladder and rotation; wave 4 the lead case, exploration, e2e and docs.

## 13. Queue: phase P-drift (HAL-*)

Priority HIGH. Reused ids: DRV-model-catalog, CC-inventory, CC-advisor, CC-hooks, CC-instruction, CC-resume-pack, CC-store, CC-digest, DX-handoff-pack, DX-fleet-registry, SW-ledger, SW-seed-pack, SW-reassign, SW-git-fallback, B-am-ingest, B-am-search, B-am-handoff, B-am-capture, DSH-session-ingest, TE-context-caps, TE-measure, P-lean-start, DRV-facts. Proposals for folds (docs only, not done): `HAL-model-facts` could fold into `DRV-facts` when it lands; `HAL-measure` can share the transcript reader of `TE-measure`; `HAL-ladder`'s nudges share `CC-hooks`; `HAL-baseline-scan` and the importer's hidden-Unicode rule (D ai-import) should share one core (the decision exists; I found no implementation in `ddflow/services/importer.py`).

| Id | Wave | Needs | Writes | What |
|---|---|---|---|---|
| HAL-decide | 1 | DX-drift-research | docs only | questions Q1-Q8 with defaults for the lead to ask (decisions file) |
| HAL-baseline-scan | 1 | DX-drift-research | `ddflow/services/drift_scan.py`, `tests/test_drift_scan.py` | the stdlib script as ddflow code, read-only, `drift scan` surface, counts only |
| HAL-detect-script | 1 | HAL-baseline-scan | `ddflow/services/drift_script.py`, `tests/test_drift_script.py` | script/mixed-word/invisible detector with allowlists, baseline and accepted records |
| HAL-model-facts | 1 | HAL-decide | `ddflow/services/model_drift_facts.py`, `ddflow/data/model_drift.toml`, `tests/test_model_drift_facts.py` | `drift_onset_tokens` fact with source/confidence/expiry; standalone until DRV-model-catalog lands |
| HAL-detect-refs | 2 | HAL-baseline-scan | `ddflow/services/drift_refs.py`, `tests/test_drift_refs.py` | fabricated reference checker: paths, ids, flags, symbols, packages |
| HAL-signals | 2 | HAL-baseline-scan | `ddflow/services/drift_signals.py`, `tests/test_drift_signals.py` | behavioural drift counters from gate, tool and transcript data per model and fill bucket |
| HAL-canary | 2 | HAL-model-facts | `ddflow/services/drift_canary.py`, `tests/test_drift_canary.py` | context-integrity probe: question generation, ground-truth scoring |
| HAL-fact-records | 2 | HAL-model-facts | `ddflow/services/model_fact_events.py`, `tests/test_model_fact_events.py` | `model.fact` events, replay, doctor/digest/catalog views, source learned/documented/operator; includes stamping served model and fill on agent events (bug Bb54029aee9) |
| HAL-ladder | 3 | HAL-detect-script, HAL-model-facts, HAL-signals | `ddflow/services/drift_ladder.py`, `tests/test_drift_ladder.py` | policy ladder, quarantine marks, onset bisect |
| HAL-hook | 3 | HAL-detect-script | `ddflow/services/drift_hooks.py`, `tests/test_drift_hooks.py` | commit-msg, pre-commit and PostToolUse integration |
| HAL-gate | 3 | HAL-detect-script, HAL-detect-refs | `ddflow/services/drift_gate.py`, `tests/test_drift_gate.py` | hygiene standards sub-result; promote to a gate later |
| HAL-digest | 3 | HAL-ladder | `ddflow/services/drift_digest.py`, `tests/test_drift_digest.py` | operator queue entries and digest section |
| HAL-rotate | 3 | HAL-ladder, CC-resume-pack, DX-handoff-pack | `ddflow/services/drift_rotate.py`, `tests/test_drift_rotate.py` | seed from ground truth, note validation, close lane, fresh lane with recording access |
| HAL-learn-onset | 3 | HAL-model-facts, HAL-signals, HAL-canary, HAL-fact-records | `ddflow/services/drift_learn.py`, `tests/test_drift_learn.py` | estimator v0: lowering-only, minimums, doctor line; bounded raising behind opt-in |
| HAL-measure | 3 | HAL-signals, HAL-fact-records | `ddflow/services/drift_ledger.py`, `tests/test_drift_ledger.py` | ledger of drift versus fill per model (shares SW-ledger shape) |
| HAL-lead-recover | 4 | HAL-rotate, HAL-canary | `ddflow/services/drift_lead.py`, `tests/test_drift_lead.py` | lead re-grounding pack, advisor text, compact instructions |
| HAL-explore | 4 | HAL-learn-onset, HAL-canary | `ddflow/services/drift_explore.py`, `tests/test_drift_explore.py` | sandbox canary lanes and exploration share, budget cap |
| HAL-e2e | 4 | HAL-rotate, HAL-gate, HAL-hook, HAL-lead-recover | `tests/test_drift_e2e.py` | scripted drifting lane: detect, quarantine, rotate, resume from last green |
| HAL-docs | 4 | HAL-e2e | `docs/ddflow/drift.md`, help topic | guide, config keys, doctor lines |

## 14. Risks

- The scanner is only as good as its allowlist; a project that legitimately writes in Russian needs `allow-script` for it, and then the same-script detector (D4) becomes necessary.
- Counting "hits" mixes weak and strong signals; weights are config and need calibration on DeepSeek transcripts that this host does not yet hold.
- Canaries cost tokens at the worst time (high fill); keep them rare, cache-aligned, and optional per model.
- Learned thresholds can encode a harness bug (a tool that corrupts output at high fill); the learner is keyed by harness for that reason.
- The operator's 350K is a single observation (**O**); until measured it is a seed, not a fact.
- Recovery depends on a recording pointer: until `B-am-ingest` exists, previous-session access is limited to the harness's own transcript path.

## 15. Sources (fetched 2026-10-26, raw; abstract pages for arXiv)

- https://arxiv.org/abs/2307.03172 · https://arxiv.org/abs/2404.06654 · https://arxiv.org/abs/2502.05167 · https://arxiv.org/abs/2412.15204 · https://arxiv.org/abs/2510.05381
- https://research.trychroma.com/context-rot (page text; figures not extracted)
- https://arxiv.org/abs/2406.10279 · https://arxiv.org/abs/2405.00253 · https://arxiv.org/abs/2404.00971 · https://arxiv.org/abs/2408.08333
- https://arxiv.org/abs/2406.20052 · https://arxiv.org/abs/2303.08896 · https://arxiv.org/abs/2302.09664
- https://arxiv.org/abs/2111.00169 · https://www.unicode.org/reports/tr39/ (v18.0.0)
- https://arxiv.org/abs/2512.02556 · https://huggingface.co/deepseek-ai/DeepSeek-V3.2 · https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro
- https://github.com/pemistahl/lingua-py · https://fasttext.cc/docs/en/language-identification.html · https://github.com/google/cld3
- ddflow: `docs/ddflow/research/agent-token-features.md`, `agent-token-features-pass2.md`, `compact-window-per-agent.md`, `context-compaction.md`, `swarm-orchestration.md`, `herdr.md`; decisions D-CC-Q4, D-session-start-budget, D-lean-review, D-model-routing, D-org-config, D-company-workflows, D-no-deletion, SW-Q10.
