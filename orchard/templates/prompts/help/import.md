# Adopting a project that already has history

A queue that starts empty tells the next agent "nothing is in flight" about a repository
with three branches in flight and forty open items in a todo file — and the agent
believes it, because the tool said so. That is worse than having no tool.

    orchard import              look; writes nothing
    orchard import --apply      write it, each item recording its source line
    orchard import --verify     is it still true, and did anyone finish it?

## What it reads

Seven sources, all optional, in the places projects actually keep them: todo checklists
(`docs/todo.md`, `docs/todo/open/*.md`, `tasks/todo.md`, `TODO.md`, `ROADMAP.md`), a
lessons corpus, `docs/adr/`, a research log, an engineering journal (`docs/log/*.md`,
`CHANGELOG.md`) dated by when entries HAPPENED, a cross-session memory store
(`.agent_memory/LOG.txt`), and branches carrying commits not on the base.

Ids are read, not invented: `### 142.A — …` and `- [ ] **WFOPT.4.6** — …` both import
under the id the project has been writing in its commit trailers for months.

## What it will not decide for you

Which open items are actually live, what each task writes, and what depends on what.
The `/import-existing-project` prompt walks through that WITH the operator. A confident
guess produces a wrong queue that the scheduler then hands out.

Guard rails: dry run by default; finished work stays out (except a completed item that
open work depends on, which comes along so the open one is not stranded); `[importer]
max_tasks` refuses a whole history; and it is idempotent, so re-running after you edit
the todo adds what is new and leaves the rest alone.

## Afterwards

`orchard import --verify` answers three things: what is imported and when; whether the
sources have moved on since; and whether anyone finished the half that needs a human —
imported tasks with no globs, and phases whose heading claims the work shipped while a
task under them is still open. Exit 2 means nothing was ever imported.
