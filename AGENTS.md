# ddflow

<!-- ddflow:begin rules/work-queue ddflow=0.2.0 fmt=1 sha=f60922c1c1f6 -->
## Work queue — ddflow

Work in this project is a queue of **phases** containing **tasks**, with declared
dependencies and declared file globs. It is managed by ddflow. The event log in
`.ddflow/events/` is the source of truth and is committed; everything else is derived.

**Start every session with `ddflow_brief`** (MCP) or `ddflow brief` (shell). It
returns any work left over from a crash, what is ready now, why everything else is
blocked, and the past lessons relevant to the task — and it replaces reading this
project's lesson and rule files.

**Claim before you edit.** `ddflow_claim` leases the item and gives you an isolated
git worktree. An unclaimed edit can be destroyed by a parallel agent.

Then: `ddflow_next` → `ddflow_claim` → work in the worktree → `ddflow_gate_status`
and satisfy each gate → `ddflow_merge` → `ddflow_complete`.

**Four rules are enforced, not requested:**

- A tool or reviewer that could not run is recorded `unavailable`, never `passed`.
- Every gate in the pipeline must carry SOME outcome before an item completes. Silence
  is not a pass; `ddflow gate skip <id> <gate> --reason "..."` is the way past one.
- At least one reviewer must come from a different model family than the author.
- A bug is not closed without a regression test that fails against the unfixed code.

**Record as you go:** the operator's words verbatim (`ddflow session prompt`), a
decision when it is settled (`ddflow decision add --globs ...`), a bug when you find it
and before you fix it, a lesson after any surprise. `ddflow replay` rebuilds this
project from those; a summary rebuilds the summary.

**Before anything non-trivial:** `ddflow recall "<what you are about to do>"`.

**Companion tools the gates expect** — `ddflow companions` says which are present and
what installing each would run: `roborev`, `codeguide-mcp`, `context7`, and a memory
server. When one is missing, **propose it to the operator early** — what it buys, what
it would run — and install it yourself if they agree. Never install without asking, and
when they decline, record that gate `unavailable` rather than passing it unaided.

**Exit codes:** `0` fine · `1` failure · `2` could not run / nothing to do · `3`
refused. Never treat `2` as `0`.

Full driver: [`docs/ddflow/drivers/implement-phase.md`](docs/ddflow/drivers/implement-phase.md) · per-agent notes: `docs/ddflow/drivers/deltas/`
<!-- ddflow:end rules/work-queue -->
