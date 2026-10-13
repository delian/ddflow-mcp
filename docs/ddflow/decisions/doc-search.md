# Documentation search: scope, index, backends, watcher

Decision document for task `B-ds-decide`, filed by `P-decisions-docs.write`. It restates the decision records below; the records in the event log are the source of truth (`ddflow decision show <id>`).

## D-doc-search: Documentation search: managed docs + doc globs (tracked only); auto watching (MCP-server watcher, watchfiles or polling, command-time checks, git hooks) through a throttled queue; RAG replies ~1500 tokens of cited, fenced snippets

status **accepted** · decided by operator · recorded by opus-lead

**Decision.** Operator 2026-10-06 (B-ds-decide answers): scope = docs check paths + [enforce].doc_globs + every document-type target, minus excludes, ddflow's generated views only on request, tracked files only; index in .ddflow/local/docs.db (derived, git-ignored); watch = auto: watcher thread in the long-running MCP server (watchfiles via the [watch] extra, else polling), cheap change checks at normal commands, git hooks; throttled coalescing queue (debounce 1.5 s, 50 files per batch, 200 ms CPU per tick), never blocking a request; FTS5 porter + trigram fused (RRF), [search]/[rag] extras optional (D-unify); an MCP answer is at most ~1500 tokens (configurable) of best-matching sections with file:line citations, fenced as data, never a whole file unless asked.

Governs: `ddflow/services/docsearch/**`, `ddflow/infra/docindex.py`
