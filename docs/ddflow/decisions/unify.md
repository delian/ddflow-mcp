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
