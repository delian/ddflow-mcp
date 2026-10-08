# D-unify — how P-unify is carried out

Status: accepted · decided by the operator, 2026-10-06 (B-uni-decide, phase P-unify)
Research: R-unify. Related: D-unify-first, D-plain-keys, D-mcp-identity-per-call.

## Context

R-unify measured the same concerns implemented many separate ways: three declarations
per command, six rankers, seven shell runners, four atomic writers, four TOML writers,
four definitions of "gate settled", nine text clippers and two redactors. It also found
that about 280 planned tasks would add many more. P-unify replaces each of these with
one shared interface. This record holds the choices only the operator could make.
The task bodies hold the engineering.

## Decision

1. **Runtime dependencies: Jinja2 and tomlkit.** tomlkit (MIT, pure Python, no
   dependencies of its own) becomes the second declared runtime dependency, so
   config write-back preserves comments and layout. It arrives through pip or uv, so
   its security updates do too. The README's "one dependency" statement becomes "two:
   Jinja2 and tomlkit". Every other library is optional.

2. **Optional extras.** There are four extras. Each is imported in exactly one adapter
   module and has a stdlib fallback. A `ddflow doctor` line says whether the extra is
   present. A feature that needs an absent extra is recorded `unavailable`. It is never
   silently downgraded and never reported as passed.
   - `ddflow[search]`: rapidfuzz, for the fuzzy rerank. Fallback: difflib plus SQLite
     FTS5 trigram, when the local SQLite supports it.
   - `ddflow[watch]`: watchfiles, for native file watching. Fallback: polling, which
     containers need in any case.
   - `ddflow[rag]`: model2vec and sqlite-vec, for local embeddings. A model is never
     downloaded at runtime; the operator points to a local model directory. Fallback:
     BM25 only.
   - `ddflow[mcp-sdk]`: the official MCP SDK. It is used only for a network
     (Streamable HTTP) transport. stdio stays on the stdlib engine.

   Development only: hypothesis, syrupy, import-linter, mutmut, networkx and
   markdown-it-py (the last two as test oracles). Avoided, for the reasons in R-unify:
   pydantic or FastMCP in core, GitPython, pygit2, dulwich, merge3 (GPL), atomicwrites,
   backoff, and networkx at runtime.

3. **Names.**
   - `ddflow docs` is the one group for documents, checks and documentation search.
     There is no separate `doc` group, and the P-docs-generic tasks use `docs`.
   - `ddflow rule` is singular. `rules` is kept as an alias.
   - Scheduled definitions are "schedules". Process jobs keep the name `job`
     (`services/jobs.py`).
   - `ddflow search` searches everything with `--source` (records, sessions, prompts,
     docs, rules, skills, agents, schedules). `recall` stays as its budgeted,
     agent-facing form, built on the same engine.
   - MCP tool names follow the same choices. The vocabulary aliases from D-id-schemes
     still apply on top.

4. **Migration: strangler with ratchets.** Before an area is touched, its behaviour is
   pinned with golden snapshots (`--help` output, MCP `tools/list`, `config --explain`,
   brief and recall output, exports) and property tests (fold, scheduler, graph).
   Each interface lands, then call sites move over in slices. A guard test counts the
   old patterns against a committed baseline, and the count can only go down:
   - raw subprocess, git argv, write_text, tempfile and hashlib calls outside their
     one module;
   - functions graded D or worse;
   - deferred imports;
   - duplicate-code clusters.

   No user-visible behaviour changes, except bug fixes and the decisions in this
   record. Any disagreement a pinning test exposes is filed as a bug, never quietly
   changed.

5. **Gates passed on refutation are allowed but flagged.** When every finding on a
   review gate was refuted with a probe, an agent may still record the gate passed.
   The gate is then marked "passed on refutation" in `gate status`, `status`, the brief
   and the completion summary, and the operator can list such gates
   (`ddflow gate list --refuted`) to spot-check. A refutation must carry a probe, which
   is already enforced.

6. **Redaction: the full profile everywhere.** One shared Redactor. The committed event
   log uses the same profile as views, exports and bug reports. That profile removes
   secrets, host names, home-directory paths and private network addresses. Existing
   events are not rewritten (the log is append-only); the profile applies to new writes.

7. **Rules are recorded events with rendered files.** Rule changes become events on
   the shared definition-record model: history, provenance, replay and the add-time
   duplicate check, as for every other record. `.ddflow/rules/*.toml` stays as a
   generated, human-readable view. A hand edit there is detected by its digest and
   imported as an event. A one-time import records today's rule files as events.

8. **Details settled with this record.**
   - A 2026-07-28 MCP client names itself per call in `_meta["ddflow/agent"]`.
   - A client that names itself this way is treated like an `as_agent` caller and
     never adopts the server's own worktree on claim (bug B7c7a0d9222).
   - Adaptive parallelism ships conservative defaults for its remaining signals:
     - free memory: shrink below 15%, pause admission below 5%;
     - free disk: shrink below 10%, pause admission below 3%;
     - reviewer latency and gate failure rate: shrink at 2x their measured baseline.

     All of these are configurable, documented and tested.
   - MCP: one engine serves the `initialize` revisions (up to 2025-11-25) and
     2026-07-28. This is already the case on main (B149, fix-Bac0bb04c9f).

## Consequences

- A second runtime dependency, chosen for correct comment-preserving config writes and
  for upstream security updates.
- Optional features degrade visibly. A missing extra is reported as unavailable, never
  as passed.
- The rules storage change needs a one-time import and a rendered-view check, like the
  other generated views.
- The stricter log profile means a committed log loses host names and home paths that
  could have helped reproduce an environment. The operator chose privacy over that.
- Gates passed on refutation stay fast, and they are visible.

## Alternatives rejected

- Vendoring tomlkit: the operator prefers upstream updates over the one-dependency
  promise.
- Keeping separate `doc` and `docs` groups.
- A big-bang rewrite of each area.
- Requiring operator approval for every gate passed on refutation.
- A lighter redaction profile for the log, or none.
- Leaving rules as unrecorded files.
- Adopting the server's worktree for a client that names itself in `_meta`.
- Leaving the memory, disk and log signals measured but never acted on.

## Re-pointing record (B-uni-repoint, 2026-10-08)

Each open, unstarted task below now needs the shared-interface task instead of building its own (existing needs kept). Reason per group:

| task | new need | group | reason |
|---|---|---|---|
| B-rules-cli | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-skills-events | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-skills-author | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-sub-events | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-sched-author | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-dt-surfaces | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-ds-surfaces | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-docs-check-command | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-quota-surfaces | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-hx-cli | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-id-surfaces | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-af-surface | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-coh-resolve | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B199 | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-rw-revise | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-rules-inventory | B-uni-record-surface | record-surface | one RecordKind descriptor generates the CRUD surface |
| B-ds-search | B-uni-search-core | search-core | docs search is a source of the one search engine |
| B-ds-rag | B-uni-search-core | search-core | docs search is a source of the one search engine |
| B-rw-inject | B-uni-context-pack | context-pack | budgeted context injection and the Embedder are shared |
| B-skills-context | B-uni-context-pack | context-pack | budgeted context injection and the Embedder are shared |
| B-rules-context-gate | B-uni-context-pack | context-pack | budgeted context injection and the Embedder are shared |
| B-rules-rag | B-uni-context-pack | context-pack | budgeted context injection and the Embedder are shared |
| B-dt-rules | B-uni-context-pack | context-pack | budgeted context injection and the Embedder are shared |
| B-ds-rag | B-uni-context-pack | context-pack | budgeted context injection and the Embedder are shared |
| B202 | B-uni-context-pack | context-pack | budgeted context injection and the Embedder are shared |
| B-ds-semantic | B-uni-context-pack | context-pack | budgeted context injection and the Embedder are shared |
| B-dt-obligations | B-uni-triggers | triggers | due/trigger evaluation is shared; the task registers a template |
| B-upstream-detectors | B-uni-triggers | triggers | due/trigger evaluation is shared; the task registers a template |
| B-bugs-fix-now | B-uni-triggers | triggers | due/trigger evaluation is shared; the task registers a template |
| B-security-pass | B-uni-triggers | triggers | due/trigger evaluation is shared; the task registers a template |
| B-dependency-upgrades | B-uni-triggers | triggers | due/trigger evaluation is shared; the task registers a template |
| B-doctor-overdue-cadences | B-uni-triggers | triggers | due/trigger evaluation is shared; the task registers a template |
| B-dt-sources | B-uni-cmdrunner | cmdrunner | configured commands run through the one CommandRunner |
| B-docs-lint-and-links | B-uni-cmdrunner | cmdrunner | configured commands run through the one CommandRunner |
| B-docscheck-extractors | B-uni-cmdrunner | cmdrunner | configured commands run through the one CommandRunner |
| B-ds-semantic | B-uni-cmdrunner | cmdrunner | configured commands run through the one CommandRunner |
| B-sched-run | B-uni-cmdrunner | cmdrunner | configured commands run through the one CommandRunner |
| B-precommit-integration | B-uni-cmdrunner | cmdrunner | configured commands run through the one CommandRunner |
| B-security-pass | B-uni-cmdrunner | cmdrunner | configured commands run through the one CommandRunner |
| B-dependency-upgrades | B-uni-cmdrunner | cmdrunner | configured commands run through the one CommandRunner |
| B-skills-install | B-uni-cmdrunner | cmdrunner | configured commands run through the one CommandRunner |
| B-upgrade.9-self-upgrade | B-uni-cmdrunner | cmdrunner | configured commands run through the one CommandRunner |
| B-dt-workflow | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-research-items | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-rw-workflow | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-sched-run | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-docs-gate-evidence | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-gate-evidence-fields | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-gate-when-globs | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-cochange-checks | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-dt-obligations | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-reviewer-pool | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-sub-route | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-rules-context-gate | B-uni-kind-pipeline | kind-pipeline | per-kind pipelines and gate predicates are data on one registry |
| B-quota-loop | B-hx-hook-core | hub | harness hook table (HookSpec) lands first; the task installs/uses hooks through it |
| B-rw-trigger | B-hx-hook-core | hub | harness hook table (HookSpec) lands first; the task installs/uses hooks through it |
| B-upgrade.6-notice | B-hx-hook-core | hub | harness hook table (HookSpec) lands first; the task installs/uses hooks through it |
| B-ds-watch | B-hx-hook-core | hub | harness hook table (HookSpec) lands first; the task installs/uses hooks through it |
| B-res-brief-recovery-link | B-hx-hook-core | hub | harness hook table (HookSpec) lands first; the task installs/uses hooks through it |
| B-skills-model | B-hx-descriptor | hub | harness descriptors land first; skills model reads the per-agent layout from them |
| B-export-security-md | B-dt-sources | exports | exported documents are doc types whose section source is kind:<export kind>; build on the doc-type sources instead of a separate export kind |
| B-sched-docs-acceptance | B-dt-sources | exports | exported documents are doc types whose section source is kind:<export kind>; build on the doc-type sources instead of a separate export kind |
| B-skills-export-docs | B-dt-sources | exports | exported documents are doc types whose section source is kind:<export kind>; build on the doc-type sources instead of a separate export kind |
| B-sub-docs | B-dt-sources | exports | exported documents are doc types whose section source is kind:<export kind>; build on the doc-type sources instead of a separate export kind |
| B-dt-library | removes B-export-security-md | exports | inverted: SECURITY.md is an export doc type built on B-dt-sources, so the library consumes it, not the reverse |

Skipped: groups whose interface task is already done (def-records, overlay, local-worker, plan-pipeline, approval) need no edge; tasks already done (B-sched-model, B-trigger-model, B-id-config, B-af-sampler) are untouched; needs already present (B-semantic-recall, B-rw-stale, B-rw-gate-link) are not repeated.
B-semantic-recall and B-ds-semantic are not merged into one task: both now consume the one Embedder of B-uni-context-pack (no infra/embed.py, no second backend).
The stray open task 'nosuch' was removed.
