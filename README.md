# ddflow

<!-- mcp-name: io.github.delian/ddflow-mcp -->

A portable, agent-agnostic **work-queue kernel** for AI coding agents.

You keep a queue of phases and tasks with declared dependencies. You say *"implement
phase P2"*. Independent tasks fan out to parallel agents in isolated git worktrees;
dependent ones wait. Every task passes a quality pipeline whose gates cannot be passed by
assertion. If an agent crashes, its work is found rather than lost. If everything except
the log is destroyed, the project's decision history rebuilds from the log alone.

**One dependency beyond `python3` and `git`** (Jinja2, for the prompt templates; see
[Extending it by writing text, not code](#extending-it-by-writing-text-not-code)). Works with Claude Code, Gemini CLI,
Codex, Copilot, Cursor, Kimi, opencode, Aider, a CI job, a Makefile, or a human at a
terminal — over a CLI
and an MCP server that are the same implementation.

---

## Instruction for an agent reader

*If you are an AI agent and were asked to read this README, this section is your
shorthand. Everything below it is detail.*

**What ddflow is.** A work queue with rules the tools enforce, for projects that agents
build. Work is phases and tasks with dependencies and declared file globs. Each task gets
a lease and its own git worktree, then passes a gate pipeline: a check that could not run
is `unavailable`, never `passed`; one reviewer must be from another model family; a bug
closes only with a regression test that failed first. Everything is recorded in an
append-only log in `.ddflow/events/`, committed with the code. ddflow writes none of your
project's code — you do; it adds only its own config, a rules block and git hooks.
Every operation is both a shell command (`ddflow …`) and an MCP tool (`ddflow_…`).

**If the project already uses it** (`.ddflow/` exists): call `ddflow_brief` (or run
`ddflow brief`) first and follow it. It replaces reading the project's rule and lesson
files. Then: `ddflow_next` → `ddflow_claim` → work only in the worktree it returns →
`ddflow_gate_status`, satisfy each gate → `ddflow_merge` → `ddflow_complete`. The full
driver is `docs/ddflow/drivers/implement-phase.md` in that project.

**To onboard a project**, ask the user first: it installs a tool, registers an MCP
server and writes files in their repository.

1. Install: `uv tool install git+https://github.com/delian/ddflow-mcp` (not on PyPI yet).
2. In the project root: `ddflow adopt --agents claude` (your agent's
   name; see [Wiring it into your agent](#wiring-it-into-your-agent)). It writes
   `.ddflow/`, the MCP registration, the commit hook and a rules block in `AGENTS.md`.
   Restart the agent session so it loads the MCP server.
3. `ddflow import` lists the plans, lessons, decisions and unmerged branches the
   repository already has, and writes nothing. Run `ddflow import --apply` only once the
   user agrees with that list.
4. `ddflow config --set gate.unit_tests.command "<their test command>"`. Then
   `ddflow companions` lists the tools the gates expect that are missing: propose them,
   never install one unasked.
5. Put the user's plan in the queue (`ddflow phase add …`, `ddflow task add … --globs …`)
   and finish with `ddflow doctor`.

Exit codes, everywhere: `0` ok · `1` failure · `2` could not run / nothing to do · `3`
refused. Never treat `2` as `0`.

---

## Introduction

### The problem it solves

An AI coding agent is good at a task and weak at a project. One agent in one session
mostly works. Run it for weeks, or run three at once, and the same failures come back:

- **Work disappears.** A session crashes or is closed mid-task, and the half-finished
  change sits in a directory nobody remembers.
- **Agents collide.** Two of them edit the same file, and the second merge quietly
  undoes the first.
- **Checks that never ran look like checks that passed.** The linter was missing, the
  reviewer endpoint was down, the tests were "run" in a summary. The agent reports
  *done*, and nothing on record says otherwise.
- **The project forgets.** Last week's hard-won lesson, the reason behind a design
  choice, the bug that was already fixed once — gone at the next session, or at the next
  context compaction in this one.
- **Nobody can say what happened.** Which instruction led to which change, and which
  review looked at it, lives in a chat transcript that no longer exists.

ddflow is the layer between you and your agents that makes those failures structurally
hard rather than a matter of discipline. It does not write code and it is not an agent.
It is a queue, a set of rules the tools enforce, and a log of everything that happened.

### What changes for you

| Without it | With ddflow |
|---|---|
| You decide what each agent does next, and keep the plan in your head or a chat. | The plan is a queue of phases and tasks with dependencies. `ddflow next` says what can start now and **why everything else is blocked**. |
| Parallel agents step on each other. | `ddflow claim` gives each task a lease and its own git worktree; tasks that declare overlapping files are refused, not merged over. |
| "Done" means the agent said so. | Every task passes a gate pipeline you configure. A gate that could not run is recorded **unavailable, never passed**; at least one reviewer must come from a **different model family** than the author; a bug cannot be closed without a regression test that failed first. |
| A crash loses work. | `ddflow recover` finds orphaned worktrees and reports what each holds. It never deletes work. |
| Every session starts from zero. | Lessons, decisions, research verdicts, bugs and your own prompts are recorded as you go. `ddflow brief` hands the agent the ones relevant to *this* task in a bounded amount of context, and `ddflow recall` searches all of it. |
| History is a transcript. | An append-only event log, committed in git. The board, the index and the reports are rebuilt from it; `ddflow replay` reconstructs the project's decisions from the log alone. |

### Who it is for

- **One developer with one agent.** A plan that survives the session, a memory that
  survives compaction, and a record of which checks really ran. The queue is useful even
  with no parallelism at all.
- **Several agents in parallel** — subagents, several terminals, several vendors. The
  dependency graph says which tasks are independent, worktrees keep them apart, and the
  merge step lands them without anyone switching the main checkout's branch.
- **A team or a CI pipeline.** The log is committed with the code, so a fresh clone
  knows the queue and its history. Read commands have a machine-readable `--json` form
  and every command returns the same four exit codes, so a Makefile or a CI job can
  drive it exactly as an agent does.

It works with the agent you already use, because everything it does is reachable
both ways: as a shell command and as an MCP tool. Use whichever your agent, script or CI
job has. Your workflow is text, not code — the gate pipeline, the
reviewer instructions and the agent-facing prompts are files in your repository that you
can edit.

### What it is not

- **Not an agent or a model.** Your agent does the work; ddflow decides what may start,
  checks what was claimed, and remembers.
- **Not a hosted service.** Everything is files in your repository and a disposable local
  cache. No account, no server to run beyond the local MCP process.
- **Not a replacement for your tests or CI.** It runs the commands you configure and
  records their real exit codes and output.

### A first run

```sh
# ddflow-mcp is not on PyPI yet; until the first release, install from the repository:
uv tool install git+https://github.com/delian/ddflow-mcp   # or: pipx install git+https://github.com/delian/ddflow-mcp
cd /path/to/your/project
ddflow adopt                        # registers the MCP server with your agents, writes .ddflow/ and a block in AGENTS.md
ddflow phase add P1 --title "Password reset"
ddflow task add P1.T1 --phase P1 --title "Reset-token endpoint" --globs 'src/auth/**'
ddflow next                          # what can start now, and why the rest is blocked
```

`adopt` registers the server you just installed, by its full path: an install that did
not come from a package index (from git, a local directory or an archive) carries a
`direct_url.json` in its metadata ([PEP 610](https://peps.python.org/pep-0610/)), and
for one of those `adopt` writes the `ddflow-mcp` installed beside its interpreter rather
than `uvx ddflow-mcp`, which would fetch from PyPI. Only an install from an index gets
`uvx`. `--launch python` still forces the interpreter-plus-`PYTHONPATH` form.

Then tell your agent *"implement phase P1"*. The driver `adopt` installed tells it to
start with `ddflow_brief`, claim the task, work in its own worktree, satisfy each gate
and land the change. ddflow cannot make an agent follow instructions, but it makes
skipping them visible: the commit hook `adopt` installs flags a commit made without a
lease (or refuses it, if you set `[enforce].commit_without_lease = "block"`), and
`ddflow complete` refuses an item whose gates carry no outcome. Watch it with
`ddflow board`, and ask `ddflow doctor` at any point whether the project is healthy.

---

## How do I…?

Every row is a command you can run in a terminal and a tool an agent can call over MCP —
the same implementation, so neither drifts from the other.

| I want to… | CLI | MCP tool |
|---|---|---|
| **see what the workflow is** | `ddflow workflow` | `ddflow_workflow` |
| **change the workflow** | `ddflow workflow pipeline task …` · `workflow gate <id> …` · `workflow drop <id>` | `ddflow_workflow_pipeline` · `_gate` · `_drop` |
| **change any setting** | `ddflow config --explain` · `--set <key> <value>` | `ddflow_configure` |
| **add a phase / a task** | `ddflow phase add P1 --title …` · `ddflow task add P1.T1 --phase P1 --globs 'src/**'` | `ddflow_phase_add` · `ddflow_task_add` |
| **get a plan into the queue** | see [From plan mode to the queue](#from-plan-mode-to-the-queue) | same |
| **know what to work on** | `ddflow next` | `ddflow_next` |
| **start a task** | `ddflow claim <id>` → work → `ddflow gate …` → `ddflow merge` → `ddflow complete` | `ddflow_claim`, `ddflow_gate_*`, `ddflow_merge`, `ddflow_complete` |
| **see everything about one item or bug** | `ddflow show <id>` (phase, task or bug id) | `ddflow_show` |
| **see progress / effort** | `ddflow progress` · `ddflow status` · `ddflow board` | `ddflow_progress` · `ddflow_status` · `ddflow_board` |
| **find out if we're going in circles** | `ddflow loops` | `ddflow_loops` |
| **record a lesson / decision / research / bug** | `ddflow lesson add` · `decision add` · `research` · `bug found\|fixed` | `ddflow_lesson_add` · `ddflow_decision_add` · `ddflow_research_add` · `ddflow_bug_*` |
| **search everything the project remembers** | `ddflow recall '<regex>'` | `ddflow_recall` |
| **check a text against what is already filed** (read-only) | `ddflow similar '<text>' [--kind bug,task,...] [--json]` -- exit 0 with candidates, 2 with none | `ddflow_similar` |
| **record what happened this session** | `ddflow session start\|prompt\|note\|end` | `ddflow_session_*` |
| **read the engineering log** | `ddflow history` | `ddflow_history` |
| **check the tooling around the gates** | `ddflow companions` | `ddflow_companions` |
| **find work a crashed agent left** | `ddflow recover` | `ddflow_recover` |
| **check the project's integrity** | `ddflow doctor` | `ddflow_doctor` |
| **rebuild everything from the log** | `ddflow replay --verify` | `ddflow_replay` |
| **invoke a workflow / a mode of your own** | `ddflow prompts list` · `prompts show <name>` | `prompts/list` · `prompts/get` |
| **see what this project left undone** | `ddflow doctor` · `ddflow status` | the [footer on tool results](#surviving-a-compaction) |
| **ask the tool to explain itself** | `ddflow help [topic]` | `ddflow_help` |

Every read command takes `--json`. Every exit code means the same thing everywhere:
`0` healthy · `1` real failure · `2` could not run / nothing to do · `3` coordination
refused. `2` is never collapsed into `0` — *"nothing is ready"* and *"everything is
fine"* are different facts, and an agent that cannot tell them apart invents work.

---

## Table of contents

- [Instruction for an agent reader](#instruction-for-an-agent-reader)
- [Introduction](#introduction)
- [How do I…?](#how-do-i)
- [Help: what it can do, and the workflow](#help-what-it-can-do-and-the-workflow)
- [The default workflow at a glance](#the-default-workflow-at-a-glance)
- [Wiring it into your agent](#wiring-it-into-your-agent)
- [From plan mode to the queue](#from-plan-mode-to-the-queue)
- [When a companion is missing](#when-a-companion-is-missing)
- [What is automated, and what is not](#what-is-automated-and-what-is-not)
- [Two ways to drive it](#two-ways-to-drive-it)
  - [Standalone: a terminal, a Makefile, CI](#standalone-a-terminal-a-makefile-ci)
  - [As an MCP server](#as-an-mcp-server)
  - [What goes in AGENTS.md / CLAUDE.md](#what-goes-in-agentsmd--claudemd)
- [The workflow, and changing it](#the-workflow-and-changing-it)
- [The rules, and where each came from](#the-rules-and-where-each-came-from)
- [Why it is built this way](#why-it-is-built-this-way)
- [Install into any project](#install-into-any-project)
  - [Docker — for operators with no Python toolchain](#docker--for-operators-with-no-python-toolchain)
  - [Extending it by writing text, not code](#extending-it-by-writing-text-not-code)
  - [Publishing and registry](#publishing-and-registry)
  - [Cutting a release](#cutting-a-release)
  - [What CI checks](#what-ci-checks)
  - [Any LLM as a reviewer — local, remote, SaaS, or a CLI](#any-llm-as-a-reviewer--local-remote-saas-or-a-cli)
  - [Companion tools](#companion-tools)
- [Adopting a project that already has history](#adopting-a-project-that-already-has-history)
  - [Verifying an import, at any time](#verifying-an-import-at-any-time)
- [The model: phases, tasks, dependencies, globs](#the-model-phases-tasks-dependencies-globs)
- [Work that changes shape while you do it](#work-that-changes-shape-while-you-do-it)
- [Architectural decisions](#architectural-decisions)
- [Recall — "have we been here before?"](#recall--have-we-been-here-before)
  - [Similar — "is this already filed?"](#similar--is-this-already-filed)
- [Status, progress, and loops](#status-progress-and-loops)
- [The task pipeline](#the-task-pipeline)
  - [Proving a gate can fail at all](#proving-a-gate-can-fail-at-all)
- [The phase pipeline](#the-phase-pipeline)
- [Human approval: a gate the agent cannot clear](#human-approval-a-gate-the-agent-cannot-clear)
- [Parallelism and coordination](#parallelism-and-coordination)
- [Gitflow, pull requests and version tags](#gitflow-pull-requests-and-version-tags)
  - [Several release lines: fixes to older majors](#several-release-lines-fixes-to-older-majors)
  - [Environment branches: promoting downstream](#environment-branches-promoting-downstream)
  - [Workflow choices: asked, recorded, defaulted on the record](#workflow-choices-asked-recorded-defaulted-on-the-record)
- [Many agents, one server: identity, state and sharing](#many-agents-one-server-identity-state-and-sharing)
  - [Is it stateless?](#is-it-stateless)
  - [Who is calling?](#who-is-calling)
  - [Can one server serve several projects?](#can-one-server-serve-several-projects)
  - [Locking, contention and measured cost](#locking-contention-and-measured-cost)
- [Crash recovery](#crash-recovery)
- [Reconstruction from logs alone](#reconstruction-from-logs-alone)
- [Lessons that check themselves](#lessons-that-check-themselves)
- [Checking that the checks are working](#checking-that-the-checks-are-working)
- [Reading the log, and why it is never compacted](#reading-the-log-and-why-it-is-never-compacted)
- [Lessons, research and bugs](#lessons-research-and-bugs)
- [Cadences](#cadences)
- [Keeping session-start cost flat](#keeping-session-start-cost-flat)
- [Agent portability](#agent-portability)
- [Keeping the two surfaces honest](#keeping-the-two-surfaces-honest)
- [Command reference](#command-reference)
- [Configuration](#configuration)
- [Testing](#testing)
- [Documentation index](#documentation-index)

---

## Help: what it can do, and the workflow

```console
$ ddflow help                 # what this is, the loop, every capability grouped
$ ddflow help workflow        # workflow · import · gates · parallel · memory · recovery · config
```

Reachable as `ddflow_help` over MCP, and that is the point: an agent connecting had 59
tool descriptions and a state-aware handshake, neither of which answers *"what is this,
and how am I meant to work here"*. A tool description explains one tool to someone who
already picked it; the handshake describes this repository right now.

Two halves, deliberately:

- **The narrative is a template** under `ddflow/templates/prompts/help/`, so
  `ddflow prompts eject`-style overriding applies — put your own
  `.ddflow/prompts/help/workflow.md` in place and the tool teaches *your* workflow.
- **The capability inventory is generated** from the live tool table. A hand-kept
  command list in a second place is the documentation-drift class, and this project has
  paid for it twice.

Three ratchets keep the prose honest, because a page recommending a flag that was
renamed is worse than no page — whoever finds nothing reads the code, and whoever finds
a wrong answer trusts it. Every command a page names must exist as a CLI leaf or an MCP
tool; every topic the index offers must resolve; and every tool must fall into a group,
so a new capability has to be classified rather than quietly dropped from an inventory
that claims to be complete.

---

## The default workflow at a glance

Three pictures of what ships by default. Every step is configurable (see
[The workflow, and changing it](#the-workflow-and-changing-it)); `ddflow workflow`
prints what *this* project actually runs.

**The agent's loop.** One item at a time: pick it, lease it, clear its gates, land it.
Each arrow labelled with an exit code is what the tool says, not a convention —
`2` means nothing to do, `3` means refused, and neither is ever read as success.

```mermaid
flowchart TD
    S["Session start<br/>ddflow brief · recover"] --> R{"Crashed agent's<br/>work left over?"}
    R -- yes --> SV["Inspect the worktree, salvage,<br/>ddflow release"] --> N
    R -- no --> N["ddflow next"]
    N -- "exit 2: nothing ready" --> W["ddflow wait<br/>sleeps until a holder lets go"] --> N
    N -- "exit 0: items ready" --> C["ddflow claim<br/>lease + isolated git worktree"]
    C -- "exit 3: refused<br/>(lease or file-glob conflict)" --> N
    C --> P["Task pipeline<br/>satisfy every gate in order"]
    P --> M["ddflow merge<br/>lands the branch from the primary checkout"]
    M --> D["ddflow complete<br/>checks every gate has an outcome<br/>+ a different-family review"]
    D -- "exit 3: refused" --> P
    D -- done --> N
```

With `[flow].integration = "pr"`, `merge` opens a pull request and parks the item in
review instead; `next` completes it when the request merges.

**The task pipeline.** Ten gates, in order. Heavy borders are the gates
`gates.required` names by default (`implement`, `unit_tests`, `merge`); the rest still
need *some* outcome — passed, failed, unavailable, partial, or an explicit skip with a
reason — because silence is not a pass.

```mermaid
flowchart LR
    subgraph A["You, the agent"]
        direction TB
        g1["1 · research<br/>falsifiable claim + probe"] --> g2["2 · rules<br/>ddflow brief --item"] --> g3["3 · implement<br/>in the item's worktree"]
    end
    subgraph X["A different model family"]
        direction TB
        g4["4 · rubber_duck<br/>try to refute it"] --> g5["5 · critic<br/>diff vs. stated intent"]
    end
    subgraph T["Tooling"]
        direction TB
        g6["6 · standards<br/>linters, architecture"] --> g7["7 · unit_tests<br/>the suite, executed"]
    end
    subgraph B["You, again"]
        direction TB
        g8["8 · bug_hunt<br/>recurring bug classes"] --> g9["9 · dedupe<br/>already exists?"]
    end
    g10(["10 · merge<br/>ddflow lands it"])
    A --> X --> T --> B --> g10

    classDef req stroke-width:3px
    class g3,g7,g10 req
```

**The phase pipeline.** A phase wraps its tasks and checks the whole before it lands:

```mermaid
flowchart LR
    p1["research<br/>the phase's open questions"] --> p2["tasks<br/>each runs its own<br/>ten-gate pipeline,<br/>in parallel where<br/>globs allow"]
    p2 --> p3["unit_tests"] --> p4["bug_hunt"] --> p5["dedupe"]
    p5 --> p6["live_test<br/>run the real thing"] --> p7["corrections"] --> p8["docs<br/>README and docs<br/>match the change"] --> p9(["merge"])
```

Details: [The task pipeline](#the-task-pipeline) ·
[The phase pipeline](#the-phase-pipeline) ·
[Parallelism and coordination](#parallelism-and-coordination) ·
[Crash recovery](#crash-recovery).

---

## Two ways to drive it

The CLI is the whole product. The MCP server is a second surface over the same
commands, and `tests/test_mcp_parity.py` fails if the two diverge — every subcommand has
a tool, every flag is reachable, and each exemption carries a written reason.

### Standalone: a terminal, a Makefile, CI

```console
$ ddflow init
$ ddflow config --set gate.unit_tests.command "python -m pytest -q"
$ ddflow phase add P1 --title "Billing" --globs "src/billing/**"
$ ddflow task add P1.T1 --phase P1 --title "Tax rules" --globs "src/billing/tax.py"

$ ddflow next                          # exit 2 = nothing actionable; exit 1 = unknown --phase
$ ddflow claim P1.T1                   # exit 3 = refused, with the reason
leased P1.T1 · worktree .ddflow-worktrees/P1.T1 · branch ddflow/P1.T1

$ cd .ddflow-worktrees/P1.T1 && ...    # do the work
$ ddflow gate status P1.T1             # what the pipeline wants next
$ ddflow gate run P1.T1 unit_tests     # runs it; the exit code IS the evidence
$ ddflow gate record P1.T1 implement --outcome passed --evidence "added tax.py"
                                       # failed/unavailable/partial/skipped also need --reason
$ ddflow complete P1.T1                # exit 3 lists whatever is unsatisfied
$ ddflow merge P1.T1
```

You get everything except the judgement. Command gates run themselves; agent gates wait
for a human to record an outcome, and `ddflow gate skip <id> <gate> --reason "..."` is
the escape hatch — recorded as a skip, never as a pass.

In CI, the exit codes are the interface:

```make
check:
	ddflow doctor        # 1 = integrity problems, each named
	ddflow workflow      # 1 = the pipeline does not hang together
	ddflow cadence       # 2 = no periodic pass is due
```

`2` is never "no problem". A job that treats it as success reports a green build for a
suite that never ran.

### As an MCP server

`ddflow mcp` speaks newline-delimited JSON-RPC over stdio. You rarely run it by hand —
`ddflow adopt` writes the launch entry into each agent's own config and leaves existing
servers alone:

**22 agents are supported.** The full table, with what each one gets, is in
[Wiring it into your agent](#wiring-it-into-your-agent).

It also copies the driver to `docs/ddflow/drivers/`, and installs the pre-commit hook
that enforces claim-before-you-edit.

**What an agent sees the moment it connects**, with no call to make:

- **Instructions**, returned inside the `initialize` result itself — and state-aware:
  what is ready, what is in flight, which setup is missing, whether this project has
  history worth importing, whether an import was left unfinished.
- **Tools** — one per CLI command.
- **Resources** — `ddflow://board`, `ddflow://brief`, `ddflow://lessons`,
  `ddflow://research`.
- **Prompts** — which a client turns into slash commands. **Tools are things an agent
  calls; prompts are things you invoke.**

**A refused call says so first.** Over MCP a tool that did not do what was asked — a claim
refused for an overlap (exit 3), or any call whose result would otherwise be its success
shape in nulls — returns a JSON body whose first key is
`"refusal": {"reason": ..., "outcome": ..., "exit": ...}`, followed by whatever the
operation actually said (a refused claim's `alternatives`). Exit 2 ("nothing") keeps its
declared keys after that lead; a result that fills its declared shape is left as the CLI's
`--json` prints it, with the reason in the second content block.

Two tools exist so an agent can orient itself without being told: `ddflow_help` (what
is this, what is the loop) and `ddflow_workflow` (what are the rules *here*).

### What goes in AGENTS.md / CLAUDE.md

`ddflow adopt` writes it as a managed block between `<!-- DDFLOW:BEGIN -->` and
`<!-- DDFLOW:END -->`. Your own prose around it is preserved; re-running updates only
what is inside. If you write it by hand, four things have to be in it:

1. **Start every session with `ddflow_brief`** (or `ddflow brief` in a shell).
2. **Claim before you edit** — `ddflow_next` → `ddflow_claim` → work in the worktree
   it creates.
3. **The loop** — `ddflow_gate_status` → satisfy each gate → `ddflow_complete` →
   `ddflow_merge`.
4. **The exit codes**, and that `2` is not success.

Without that block an agent sees the tools and has no reason to reach for them before
editing. The block is what makes the queue authoritative rather than optional — and it
is 232 words, because an instruction file nobody finishes reading is one nobody follows.

---

## The workflow, and changing it

```console
$ ddflow workflow
# The workflow this project runs

   1. research      agent
   2. rules         agent
   3. implement     agent       (required)
   4. lint          command     (required, NOT proven able to fail)
      $ ruff check .
   ...

## The rules, and where each came from

  gates.require_outcome                  True              [default]
  gates.enforce_order                    block             [file]
  schedule.max_parallel_tasks            4                 [default]
```

One answer to "what are the rules here": every gate in order, which are commands and
which you perform, which are required, which need evidence, which need a
different-family reviewer, which have been **proven able to fail** — plus the
completion rules, the caps, the reviewers, and **where each value came from**, so a
deliberate choice is distinguishable from a default nobody touched.

### Changing it

```console
$ ddflow workflow gate lint --command "ruff check ." --into task --after implement --required
$ ddflow workflow pipeline task research,implement,lint,unit_tests,merge
$ ddflow workflow drop dedupe
```

All four reach MCP — `ddflow_workflow`, `ddflow_workflow_pipeline`,
`ddflow_workflow_gate`, `ddflow_workflow_drop` — so an agent can change the workflow
*with the operator's agreement*. Their descriptions say to ask first and offer
`dry_run`, because a pipeline governs every future item, not the one in hand.

Nothing is written until it is checked, and the order is the point: compose the change,
validate the **result**, then replace the file atomically.

- **A pipeline naming an undefined gate is refused**, naming the near miss. That one is
  otherwise silent and permanent: the outcome folds to empty, completion refuses it
  forever, and `gate record` rejects the id as unknown — so the item can never be
  completed at all, and nothing says why.
- **An unknown section or knob is refused**, with a suggestion. `[gatez]` is valid TOML
  and used to be written happily, breaking every later command — the write path
  validated the merged text for *syntax* and then validated the config already on
  *disk*, which is a writer checking the state it is replacing.
- **Dropping a gate takes it out of `required` too**, or it becomes a requirement that
  quietly requires nothing.

`ddflow workflow` and `ddflow doctor` both re-run those checks against what is on
disk. Everything is a file you can also edit by hand: gates in `[gate.<id>]`, reviewers
in `[[reviewer]]`, companions in `.ddflow/companions.toml`, and every prompt —
including the instructions your agent receives at connect — under `.ddflow/prompts/`.

**One caveat with MCP:** the connection instructions are computed once, when the server
starts. A workflow changed mid-session is live for every tool call immediately, but the
text the agent was handed is stale. Tell it to call `ddflow_workflow`, or restart.

---

## Why it is built this way

**The append-only event log is the source of truth; everything else is a projection that
can be deleted and re-derived.** The SQLite index, the markdown boards, the search index,
the recovery bundle — all disposable, all rebuilt by `ddflow rebuild`.

That single inversion is what makes the four hard properties fall out for free rather
than needing to be engineered:

| You get | Because |
|---|---|
| Two agents on two branches never conflict | Each appends to its own file. Measured: a real two-branch merge resolves clean. |
| A crashed agent loses nothing | State is folded, never written. Nothing is half-updated. |
| The project rebuilds from the log | Operator prompts *are* events. |
| An edited history is detectable | Event ids are content addresses. |

The design decisions, with the probes that settled each, are in
**[docs/RESEARCH.md](docs/RESEARCH.md)**. The two that most shaped it:

- **SQLite-on-NFS is correct here but 36× slower** than local (measured, 12 processes ×
  40 increments). So the log is authoritative and the database is a disposable cache —
  which also happens to be the choice that stays correct on filesystems where locking
  *is* broken.
- **An expired lease must never be reclaimed automatically.** A crashed agent's worktree
  is sometimes irreplaceable work and sometimes a superseded draft, and nothing in the
  metadata distinguishes them. Recovery measures and advises; it never deletes.

---

## Install into any project

**One line in your agent's MCP config. Nothing else.**

> Until the first release is on PyPI, `uvx ddflow-mcp` has nothing to fetch: install from
> the repository and run `ddflow adopt`, which registers that installation instead, as in
> [A first run](#a-first-run).

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

`uvx` fetches and runs the published package in an ephemeral environment on first use —
no clone, no virtualenv, no `PYTHONPATH`, no install step for an operator to forget, and
no vendored copy to drift from upstream. ddflow needs **one runtime dependency**
beyond `python3` and `git` (Jinja2), which is what lets it install inside
sandboxes, CI images and other tools' ephemeral containers.

Then, from the agent, with no shell at all:

| Call | What it does |
|---|---|
| `ddflow_setup` | creates `.ddflow/`, writes the driver and the `AGENTS.md` section |
| `ddflow_configure` with `toml: '[gate.unit_tests]\ncommand = "pytest -q"'` | sets your test command |
| `ddflow_reviewers_detect` with `write: true` | finds a local model server and registers it as a cross-family reviewer |
| `ddflow_phase_add`, `ddflow_task_add` | fill the queue |
| `ddflow_brief` | start every session here |

That is the whole adoption. **The per-project instruction text is 232 words** — a
managed block in `AGENTS.md`, because the MCP tool descriptions already carry the
how, and a second copy of that would drift from the one the model actually reads.

<details><summary>Shell / CI installation, and running from a source checkout</summary>

```sh
uv tool install ddflow-mcp        # or: pipx install ddflow-mcp
cd /path/to/your/project
ddflow adopt            # every supported agent
ddflow adopt --agents claude,cursor,vscode,kimi   # or name the ones you use
```

After upgrading ddflow, `ddflow doctor` notes any driver doc (`implement-phase.md`, an
adopted agent's delta) that differs byte-for-byte from the template the running ddflow
ships. `ddflow adopt --refresh-docs` (MCP: `ddflow_setup` with `refresh_docs`) rewrites
only those docs, the AGENTS.md/CLAUDE.md blocks and the agents' native rules -- never the
MCP launch, hooks or command files -- and refuses a project that was never adopted.

`adopt` is idempotent and writes managed blocks, so re-running after an upgrade updates
them and leaves your own prose alone. It writes the MCP registration into each agent's
own config location, **merged** with whatever servers are already there. From a source
checkout it points the config at that checkout instead of the published package, so
developing ddflow does not silently configure your project against the released
version.

</details>

### Docker — for operators with no Python toolchain

```json
{ "mcpServers": { "ddflow": { "command": "docker", "args": [
    "run", "-i", "--rm",
    "-v", "${workspaceFolder}:/repo",
    "--add-host=host.docker.internal:host-gateway",
    "ghcr.io/delian/ddflow-mcp:latest" ] } } }
```

`ddflow adopt --launch docker` writes exactly that. The image is **107 MB** (Alpine;
ddflow is pure standard library, so there is no compiled dependency to worry musl
about) and behaves identically on Linux, macOS and Windows.

Four things go wrong when a containerised tool touches a bind-mounted git repo. All
four are silent, one of them loses work, and all four are handled:

| Trap | What it looks like | Handled by |
|---|---|---|
| **Worktrees land outside the mount** | `worktree.root` defaults to `../.ddflow-worktrees`, a sibling of the repo. In a container only the repo is mounted, so worktrees go to the ephemeral layer and **are destroyed on exit with the agent's uncommitted work inside them.** | `container.default_worktree_root` relocates a sibling root to `.ddflow-worktrees` inside the repo, and `adopt` gitignores it |
| **Root-owned files** | On a Linux bind mount the operator needs `sudo` to edit their own project afterwards | the entrypoint reads the mount's uid/gid and `su-exec`s down to it |
| **git refuses the mount** | "detected dubious ownership", surfacing as an unexplained ddflow failure | `safe.directory` set in the entrypoint |
| **No git identity** | `git commit` fails with "Please tell me who you are" | entrypoint prefers `GIT_AUTHOR_*`, then the repo's own config, then a clearly-marked placeholder |

And one that cannot be fully handled, so it is reported: **`127.0.0.1` inside a
container is the container.** A model server on your own machine is not reachable from
there. ddflow rewrites loopback reviewer URLs to `host.docker.internal`, and `ddflow
doctor` tells you that on Linux you must also pass
`--add-host=host.docker.internal:host-gateway`, because unlike Docker Desktop the Linux
engine does not provide that name.

> **The related portability fix:** worktree paths are stored in the event log
> **relative to the repo root**. The log is committed and shared, so an absolute path is
> true only on the machine that wrote it — false for a teammate who cloned elsewhere,
> for CI, and for a container where the repo is `/repo`. Pinned by
> `test_the_event_log_carries_no_absolute_paths`.

### Extending it by writing text, not code

Every prompt is an external template, resolved config → project → shipped:

```sh
ddflow prompts list              # where each template currently comes from
ddflow prompts eject             # copy the shipped ones into .ddflow/prompts/
$EDITOR .ddflow/prompts/review_system.md
```

**Adding a mode of your own: `[[macro]]`.** Overriding a shipped workflow needs no code,
and neither does adding one. A macro is a named, parameterised prompt — "enter debugger
mode" — that appears everywhere the shipped workflows do: `prompts/list` and `prompts/get`
over MCP, which is what a client turns into a slash command, and `ddflow prompts list|show`
in a terminal.

```toml
# .ddflow/config.toml   (or .ddflow/macros.toml, if you prefer to split it out)
[[macro]]
name = "debugger"
title = "Enter debugger mode"
description = "Reproduce first, then bisect. No fix without a failing probe."
params = ["symptom"]                                  # required, not optional
tools  = ["ddflow_bug_found", "ddflow_gate_run", "ddflow_bug_fixed"]
prompt = """
You are debugging: {{ symptom }}

Reproduce it before you theorise. Paste the command and its output.
"""
```

Use `prompt_file = "docs/modes/debugger.md"` instead for anything long enough that TOML
quoting gets in the way.

**When to reach for a macro rather than a gate.** A gate is a step every item passes
through, recorded against that item and blocking its completion. A macro is a MODE an
operator enters, belonging to no item and recorded nowhere — "audit this release", "handle
this incident". If the thing should hold up a task until it is done, it is a gate; if it is
a way of working you want to name and re-enter, it is a macro. Putting a mode in the
pipeline makes every task wait for something that was never about that task.

**`tools` is declarative, not a sandbox.** It is rendered into the prompt as the ordered
set the mode expects, so the agent is told what the mode is for and the next reader can
tell what it was supposed to do. It does **not** restrict what the agent may call — MCP has
no mechanism for that, and claiming a security property this cannot honour would be worse
than not having it. This is the deliberate departure from
[`dx-zero/mcpn`](https://github.com/dx-zero/mcpn), whose `toolMode: situational` lets the
model pick freely from a bound set with no recorded ordering: a session you cannot replay
is a session you cannot review, which is the property the event log exists to give you.

Three things a macro refuses, because each alternative fails quietly: a **missing
parameter** (a prompt with a hole in it reads as a complete instruction), a **name that
belongs to a shipped command** (silent shadowing leaves you editing a block that does
nothing), and **both `prompt` and `prompt_file`** (two sources for one body means one is
dead and looks live). A refused macro is refused **alone and by name** — the others still
load — and `ddflow prompts list`, `prompts show`, `doctor` and the MCP `prompts/list` say
which one and why; an undecodable `prompt_file` is a named problem too.

**Including the one the agent actually reads first.** `mcp_instructions.md` is the block
an MCP client injects into the model's context on connect — the workflow, the reporting
duties, and which companion tools to reach for. It is the file to edit when you want
this project to work differently:

```sh
ddflow prompts eject mcp_instructions
$EDITOR .ddflow/prompts/mcp_instructions.md      # or [prompts] mcp_instructions = "..."
```

It renders against the live state — `adopted`, `task_pipeline`, `setup_todo`,
`companions`, `missing_companions`, `gate_gaps`, `recoverable`, `loops` — so the
instruction is the next concrete action rather than a fixed blurb the model learns to
skip. A broken override **says so in the instruction block itself** instead of falling
back to the default: this is the one surface where nobody would ever notice their edit
was not live.

Templates render with **Jinja2**, which is ddflow's one runtime dependency, and with a
strict standard-library renderer when it is absent — a stripped deployment with no
reachable package index still starts. The shipped templates use the subset both engines
agree on, and `tests/test_template_engines.py` walks the template REGISTRY, rendering
every entry through both engines and asserting the outputs are byte-identical.

That test is iterated rather than hand-listed for a reason. Its predecessor named three
templates in a dict, `mcp_instructions.md` was never added, and in 0.1.1 the largest and
most important template rendered correctly under Jinja2 and failed under the fallback —
so the entire MCP handshake for an *unadopted* repository, the first thing a new user
ever sees, degraded to `ddflow's instruction template could not be loaded`. Jinja2 was
not a declared dependency at the time, so developers had it and the project venv did
not: `python -m pytest` was green and `uv run pytest` was red on the same commit.

The fallback now **raises** on any construct it does not implement rather than copying
it through. The old regex engine emitted what it could not parse, so a condition as
ordinary as `{% if a or b %}` — which its single-name pattern never matched — reached
the client as literal template source.

Both renderers are **strict about undefined variables**: a prompt silently missing the
diff it was supposed to carry is the vacuous review in template form — the model
dutifully reviews nothing and reports no findings.

The rest is TOML: gates and their pipelines (`[gate.*]`, `gates.task_pipeline`),
reviewers (`[[reviewer]]`), companions (`[[companion]]`), enforcement (`[enforce]`),
cadences, and the rest of the 139 knobs.
`ddflow config --set <key> <value>` edits one key in place, preserving comments.

#### What is committed, and what stays on your machine

ddflow **recommends** services; it never ships one person's configuration. Two layers:

| Layer | Files | Holds |
|---|---|---|
| committed | `.ddflow/config.toml`, `.ddflow/gates.toml` | generic project policy: the test command, the pipelines, the gates (human checkpoints included) — what every clone must agree on |
| machine-local, git-ignored | `.ddflow/local/config.toml`, `.ddflow/local/gates.toml`, `.ddflow/local/reviewers.toml` | *your* services: reviewer endpoints, model names of a private deployment, API-key variable names, LAN hosts, a worker count sized to this machine |

The local files are read **last**, so they win; `ddflow config --explain` reports such a
value's source as `local`. Every writer of an operator-specific value targets the local
layer by default:

```sh
ddflow reviewers add --preset ollama --model qwen3:8b   # -> .ddflow/local/reviewers.toml
ddflow reviewers detect --write                         # -> .ddflow/local/reviewers.toml
ddflow config --local --set gate.unit_tests.command "pytest -q -n 48"
ddflow config --local --append-toml "$(cat my-reviewer.toml)"
```

`--shared` on `reviewers add` / `reviewers detect --write` commits the block to
`.ddflow/config.toml` instead — only for a service every clone reaches at the same
address. `config --set` and `--append-toml` stay committed unless you pass `--local`
(over MCP: `ddflow_configure` with `local=true`, `ddflow_reviewers_detect` with
`shared=true`), because a test command or a pipeline is project policy. The human-gate
guards hold on both layers: no writer sets `gate.<id>.human`, and none can drop a human
gate from a pipeline. `.ddflow/local/` carries its own `*` `.gitignore`, so it stays
uncommitted even in a project whose `.ddflow/.gitignore` predates it. API keys are never
written anywhere — only the *name* of the variable that holds one.

### Publishing and registry

**Nobody should have to paste JSON into an IDE to use this.** `server.json` is the
[MCP registry](https://modelcontextprotocol.io/registry/quickstart) manifest
(`io.github.delian/ddflow-mcp`), and publishing it is what makes ddflow findable in the
VS Code and Cursor marketplaces rather than something you configure by hand. It offers
three ways to run the same server, so a client picks whichever it supports:

| Package | Identifier | For |
|---|---|---|
| `pypi` | `ddflow-mcp`, `runtimeHint: uvx` | Anything with `uv` — no clone, no install step |
| `oci` | `docker.io/delian/ddflow-mcp:<version>` | Operators with no Python toolchain |
| `oci` | `ghcr.io/delian/ddflow-mcp:<version>` | The same image, no Docker Hub account needed |

The image is built for `amd64` and `arm64`, because an Apple-silicon operator running it
under emulation pays that cost on every tool call, and tool calls are all this server
does.

**How CI authenticates — four mechanisms, one stored secret:**

| Target | Mechanism | Stored secret? | Setup |
|---|---|---|---|
| PyPI | OIDC trusted publishing (`id-token: write`) | No | Add a trusted publisher on PyPI, once |
| ghcr.io | `GITHUB_TOKEN`, injected per run, expires with the job | No | none |
| Docker Hub | `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN` | **Yes** | Create an access token, add both secrets |
| MCP registry | GitHub OIDC — proves control of the account that owns the `io.github.delian/*` namespace | No | none |
| tag + release | `GITHUB_TOKEN` (`contents: write`) | No | none |

**Docker Hub is the only one that needs a long-lived credential**, because it has no OIDC
equivalent. Use an access token scoped to this repository, never an account password. If
that is one secret too many, delete the Docker Hub login and its two tags — ghcr.io alone
satisfies the OCI entries a marketplace needs, and `server.json` lists both so a client
picks whichever resolves.

`environment: release` on the publishing jobs is a control worth knowing about: point it
at a GitHub environment with required reviewers and every release waits for a human,
with no change to the workflow.

**Order matters and the workflow encodes it.** `mcp-publisher` validates that every
package named in the manifest exists, so the registry step runs *after* both PyPI and
Docker — publishing the manifest first would advertise a version nobody can fetch.

**The registry verifies ownership, and the workflow checks it first.** The registry accepts
a package only when the artefact itself names the server: the PyPI package's README
carries `<!-- mcp-name: io.github.delian/ddflow-mcp -->` (the first lines of this file),
and each image carries the label `io.modelcontextprotocol.server.name` with the same name.
The `verify` job runs `tests/test_registry_ownership.py` first, so a release the registry
would reject — a missing marker, or a label that does not match — is refused before
anything is uploaded; a `server.json` `description` over the registry's 100-character limit
fails the suite the same way. The publish step also retries a *transient* registry failure
(a 504, a 408/429, a network error) with backoff, checking the registry for the exact
version after every attempt, because the publish behind a 504 may have committed; a 4xx
that retrying cannot fix fails at once, and only after the budget does it fail with a
"Re-run failed jobs" hint.

Four things gate a release, and each exists because the failure it catches is public and
irreversible:

* the tag, `pyproject.toml`, `server.json`'s version **and every OCI identifier's tag**
  must agree — a `:0.1.0` left behind while `version` moved on publishes a manifest
  pointing at the previous image, installable and wrong;
* the full suite, plus the slow end-to-end scenarios, which `-m 'not slow'` otherwise
  excludes from every ordinary run;
* the wheel must **install into a clean venv and run**, and carry its templates —
  `uv build` succeeding proves the metadata parses, not that `ddflow help` works;
* the image must answer `initialize` over stdio. A built image that cannot is a broken
  release every marketplace will happily offer.

### Cutting a release

```console
$ git push origin main           # that is the whole release
```

**Every push to main that changes shipped code releases, with the PATCH version bumped.**
Major and minor move only when you move them.
"Shipped" means `ddflow/`, `pyproject.toml`, `uv.lock`, `Dockerfile`,
`docker-entrypoint.sh`, `.dockerignore`, `server.json` or `README.md` (the PyPI page, and
the line the registry verifies): a push of other docs, tests or the ddflow event log
releases nothing, because PyPI keeps every version forever and one identical to the last
is noise nobody can withdraw. CI runs `scripts/bump.sh patch`, commits `release 0.1.2` to
main, publishes PyPI, Docker Hub, ghcr.io and the MCP registry, then creates `v0.1.2` and
a GitHub release — **last**, and only once every publish succeeded, because a tag pointing
at a half-release is worse than no tag: it looks authoritative.

**Pull after a release.** The release commit is CI's, so your `main` is one commit behind
it and the next push is refused until you `git pull`.

How the gate picks the version: it publishes the declared version if PyPI does not have it
yet, and bumps patch only if it does. So moving major or minor is yours to do —

```console
$ scripts/bump.sh minor          # 0.1.4 -> 0.2.0 (or: major, or an exact 1.0.0)
$ git commit -am 'release 0.2.0' && git push origin main
```

— and CI publishes exactly `0.2.0`; the next push that bumps nothing releases `0.2.1`.

The same rule makes a release that failed *before* its PyPI upload retry its number with
the next push. One that failed after it — Docker Hub, ghcr.io, the MCP registry — does
not: PyPI has the number, so the next push moves past it; re-run that run's failed jobs
from the Actions page instead. A bump never lands on a number PyPI already holds (a `v*`
tag can publish one out of band): CI skips to the next free patch before it commits
anything. The bump is pushed to main *before* anything publishes: a push that
loses a race with another commit fails the run and publishes nothing, where pushed last it
would leave PyPI holding a version main does not declare. Runs are serialized, and only
`main` or a `v*` tag releases.

The version lives in seven places — `pyproject.toml`, `server.json`'s version, its
per-package version, the tag inside every OCI identifier, `SERVER_INFO` (what the server
tells every client it is), `ddflow.__version__`, and `uv.lock`, which records the project's
own version. `scripts/bump.sh` moves them all and re-reads them to check it did;
`tests/test_packaging.py` fails if the first six ever drift. (That test caught the bump
script missing `SERVER_INFO` on its first run.)

**`scripts/release.sh` runs all of that locally and publishes nothing.** It is dry by
default, needs no credentials, and exists because a tag is not reversible: PyPI refuses a
re-upload, `:latest` is on someone's disk before you notice, and a registry manifest is
what an IDE offers people. If it fails on your laptop, the tag was going to fail an hour
later in public. `--publish` is the escape hatch for when CI is unavailable, and it makes
you type the version to confirm.

### What CI checks

`.github/workflows/ci.yml` runs on every push and pull request, in four jobs that fail
for different reasons so you can tell at a glance which:

| Job | Checks |
|---|---|
| **quality** | `ruff check` + `format --check`; the wheel **installs into a clean venv, runs, and carries its templates**; `gitleaks` over full history; `bandit` over the package; a dependency audit that also asserts every runtime dependency is on an explicit allowlist (today: Jinja2) |
| **tests** | The suite on Python 3.11 and 3.13 — the floor and the current release, because a version-specific break is a break for somebody |
| **codeql** | GitHub's `security-and-quality` queries, landing in the Security tab rather than a log |
| **scenarios** | The slow end-to-end runs, and the concurrency/load suite, each as its own step with `if: always()` |

Two of those exist because of specific failures. The wheel check is there because `uv
build` succeeding proves the metadata parses, not that `ddflow help` works — a wheel
missing its templates fails on the user's machine. And **`gitleaks` is there because this
project has already committed a live API key**: a secret in git is a leaked secret,
rotation is the only remedy, so the check that matters is the one that runs before every
push.

`bandit` deliberately skips `tests/`, which use `subprocess` and temporary paths
constantly and by design. A scanner that cries wolf on every fixture is a scanner nobody
reads.

**The same checks run before you push.** Install the pre-push hook once per clone:

```console
$ ln -sf ../../scripts/ci/pre-push "$(git rev-parse --git-common-dir)/hooks/pre-push"
```

It runs what CI runs, from `.pre-commit-config.yaml`: ruff, the wheel probe, bandit, the
dependency allowlist, gitleaks over the commits being pushed, the unit suite and the
scenarios, and a schema check of the workflow files. The build probe and the allowlist are
`scripts/ci/` files that CI calls too, so the two cannot drift.

It runs them **in a scratch worktree of the commit being pushed**, not in your checkout —
which is why it is not `pre-commit install`. pre-commit fails a hook when anything in the
tree changes while it runs, and ddflow agents append to the committed event logs
constantly: in your own tree the test hook failed with every test green. A scratch tree has
no other writers, and it checks what you push rather than what is uncommitted.

The unit suite and the scenarios run as **one** pytest session, one worker per CPU
(`-n auto`) with work-stealing, so the scenarios overlap the unit tests instead of
following them: about a minute on a 192-thread machine. The scenarios take CI's
no-model-server path (`DDFLOW_DEMO_NO_REVIEWER=1`), since a real review there costs
minutes and is not what CI checks.

It is a push hook, not a commit hook, because ddflow's own hooks own the commit (`ddflow
hooks install`), and because minutes per commit teaches `--no-verify`. `SKIP=tests git
push` skips one check by id. It does not run the 3.11/3.13 matrix, CodeQL, pip-audit or
the load suite.

### Any LLM as a reviewer — local, remote, SaaS, or a CLI

The `critic` and `rubber_duck` gates are **run by ddflow, not claimed by the agent**.
Point them at whatever you have:

```sh
ddflow reviewers presets            # 19 ready-made provider settings
ddflow reviewers add --preset ollama --model qwen3:8b
ddflow reviewers detect --write     # probe local ports, register what is serving (machine-local)
ddflow reviewers test               # send a known-buggy diff, check the reply
```

Four backends, because "any LLM" means four wire formats in practice:

| `kind` | Reaches | Examples |
|---|---|---|
| `openai` *(default)* | anything OpenAI-compatible — which is most things | ollama, vLLM, LM Studio, llama.cpp, sglang, LiteLLM, OpenAI, DeepSeek, Groq, Together, Fireworks, Mistral, OpenRouter, xAI |
| `anthropic` | the Messages API (system is a top-level field, not a message) | Claude |
| `gemini` | `generateContent` (key in the query string, not a header) | Gemini |
| `command` | **anything at all** — a CLI that reads a prompt on stdin and writes the reply to stdout | `claude -p`, `gemini -p`, `codex exec`, `llm -m`, your own script |

`command` is the escape hatch that makes the answer to "can it use *X*?" always yes: a
model with no HTTP API, behind a corporate gateway, or wrapped in an in-house tool is
still usable, with no SDK and no dependency.

**Who may add a reviewer.** A reviewer decides whether a review counts as independent, so
an agent must not be able to mint one (decision D-reviewer-trust):

- A `kind = "command"` reviewer runs any program and can print any verdict, so **only a
  person adds one**: by editing `.ddflow/local/reviewers.toml`, or with `ddflow reviewers
  add` from their own terminal. Every agent surface refuses it — `ddflow_configure`,
  `config --append-toml`, and any command run under `--agent` or `DDFLOW_AGENT`, or in a
  Claude Code shell (`CLAUDECODE`, the one harness marker known for certain; another
  harness is recognised by the `--agent`/`DDFLOW_AGENT` its ddflow setup passes).
- A reviewer **a tool writes** (`reviewers add`, `reviewers detect --write`,
  `ddflow_configure`) is recorded with who wrote it, and its reviews do not count toward
  the cross-family rule until a person checks the entry and runs
  `ddflow reviewers approve <name>` (`ddflow reviewers approve` alone lists what is
  waiting). Approval is for the entry as it was: a tool changing its endpoint, model,
  family or command makes a new, unapproved reviewer; tuning `max_tokens` or `hedge` does
  not. `reviewers approve` has no MCP tool and refuses under an agent identity.
- A reviewer **no tool wrote** — every entry configured by hand, including all of them
  from before this rule — counts exactly as before.

Like human gates, this makes a forged reviewer visible in the log; it cannot stop a shell
edit of the local files.

```toml
[[reviewer]]
name   = "local-qwen"
kind   = "openai"
base_url = "http://127.0.0.1:11434/v1"
model  = "qwen3:8b"
family = "alibaba"              # must differ from the author's family
gates  = ["critic"]
# Optional: start it if it is not already running.
launch = { command = "ollama serve", ready_url = "http://127.0.0.1:11434/v1/models" }

[[reviewer]]
name    = "claude-via-cli"
kind    = "command"
command = "claude -p --model {model}"
model   = "claude-sonnet-5"
family  = "anthropic"
gates   = ["rubber_duck"]
```

Auto-launch is **opt-in per reviewer** — starting a multi-gigabyte model server as a
side effect of asking for a code review is a surprise nobody wants by default. When it
fails it never leaves a half-started process behind, because a reviewer stuck "starting"
forever is indistinguishable from one that is down except that it also holds a process.

**Keys are never written to the config.** Only `api_key_env`, the *name* of an
environment variable — the config file is committed, and a key in git is a leaked key.

Every way of not reviewing is reported distinctly, with its remedy: no key names the
variable, a missing CLI names the binary, a dead server names the launch block you could
add, a non-zero exit shows stderr, **and empty output on exit 0 is UNAVAILABLE rather
than "no findings"** — the vacuous pass arriving by the most innocent-looking path
there is.

> **Reasoning models need a large `max_tokens`.** Default 32000, measured not guessed:
> on Qwen3.8-Flash-Next over a 30 KB diff, a 6000-token budget produced **zero
> characters of content** — the whole budget went to reasoning and the reply was
> truncated. That case is reported as `TRUNCATED` with the remedy named, never as an
> empty completion and never as a clean review. A chunk lost that way on every copy is
> tried once more, split in halves when it splits, before it is reported unreviewed.
>
> **Leave `temperature` unset** unless you mean to override the model. Unset, the request
> carries none and the server applies the model's own recommended sampling. A reasoning
> model sampled cold (ddflow used to send 0.3) can loop — "let me reconsider" — until
> the whole budget is gone; at its vendor's recommended 1.0 the same diff reviewed fully.

**Reviews run in parallel, and each chunk is raced.** A large diff is reviewed in chunks
(`max_chunk_chars`). They go out together — by default every chunk and every copy in one
wave (up to 32 requests); `max_concurrency` caps it — so a review takes as long as its
slowest chunk rather than the sum. Each
chunk is also sent `hedge` times (default 2): the first copy that answers **on contract**
is the chunk's review, and the rest are cancelled — the connection is closed so the
server aborts the request, a `command` reviewer's process group is killed. A chunk whose
every copy failed is reported exactly as before; nothing is counted as reviewed that was
not.

Why: a reasoning model's time is its reasoning length, and that is random. Measured on a
LAN vLLM: the same 3 KB diff took 64–391 s across eight identical calls, about one call
in four on a hard diff never answers before `max_tokens`, and the server was idle —
eight concurrent requests each ran 28% slower for 5.7× the throughput. A 24 KB review
went from 1 274 s (chunks in sequence) to the time of its slowest chunk.

```toml
[[reviewer]]
# ...
hedge = 3             # copies per chunk; 1 turns racing off
max_concurrency = 10  # cap on requests in flight; 0 (default) = all in one wave
```

On a **metered API** a cancelled copy is still billed for what it generated before it
was stopped: `hedge = 1` if cost matters more than time there. On a single-slot local
server (an `ollama` with `OLLAMA_NUM_PARALLEL=1`) the extra copies queue behind the first
ones, which are always sent first; there, set `max_concurrency` to the server's slots so a
queued request's `timeout_s` does not run out while it waits.

**What a review reports, and how to finish one that was partial.** Every chunk that was not
reviewed is named, with its files and its cause: a chunk whose generation ran out the
clock is labelled *did not converge* (the model was reachable), distinct from an endpoint
that was down. No chunk is header-only and none is dropped; hunk-header context is stripped
so a finding cannot cite a function the diff does not touch. Progress is printed per chunk,
the caller's lease is renewed while the review runs, and `extra_rules` on a `[[reviewer]]`
reaches its prompt. Findings are numbered `#1..#N`.

```sh
ddflow review T1 --gate critic --chunk 2,5      # re-review only chunks 2 and 5 of that review
ddflow review triage T1 --gate critic --finding 3 --refuted --probe "tests/test_x.py::t shows..."
ddflow review triage T1 --gate critic --finding 1 --confirmed --probe "fixed in 4f2a, test_y"
```

`--chunk` re-runs the named chunks of the **same cut** — the same diff, `max_chunk_chars`
and reviewer, checked before anything is sent — and merges their coverage into the gate's
recorded outcome. `review triage` records what became of each finding as its own event
(`review.triaged`): *refuted*, with the run that shows it false, or *confirmed*, with the
fix or test that answers it. The gate's outcome is not changed — a review that reported
findings stays `failed`, and that does not block completion; the log now shows what became
of each finding. Triage appears in `gate status` and `show`, and a re-review carries it over
only for a finding whose text is identical (decision D-review-triage). Over MCP it is
`ddflow_review_triage`; `--chunk` is an argument of `ddflow_review`.

### Companion tools

ddflow imposes the order and demands the evidence. It does not *perform* the judgement
inside most of its gates: `standards` wants an automated standards review, `research`
wants documentation to check a claim against. (`rules` wants memory of the last time
somebody hit this, and ddflow serves that one itself: `ddflow brief` shows the lessons
and the operational memory.) A project that installs ddflow and stops has the others
wired to nothing — and because an agent gate passes on an assertion, that gap is invisible in
exactly the way the rest of this design exists to prevent.

So the gap is **named**:

```console
$ ddflow companions
Companion tools

  [x] context7   Current library documentation
       gates: research, standards
       registered for: claude, cursor
  [x] roborev    Automated second-opinion code review
       gates: standards, bug_hunt, dedupe
       installed (roborev 0.9.1). A cli tool — the agent shells out to it, so
       there is nothing to register.
  [+] codeguide  Language and framework coding standards
       gates: standards
       installed (…) but no agent is configured to launch it.
       -> ddflow companions add --id codeguide
  [ ] sequential Structured step-by-step reasoning
       gates: research, rubber_duck, bug_hunt
       not here: `npx --no-install @modelcontextprotocol/server-sequential-thinking` exited 1
       -> ask the operator, then: npx -y @modelcontextprotocol/server-sequential-thinking

Gates in this project's task pipeline with no companion behind them:
  implement, rubber_duck, critic, unit_tests, bug_hunt, dedupe, merge
```

A companion counts as **registered** only when an entry under its id can actually launch
something: a bare or junk table with its name does not hide a real launch registered
elsewhere, and does not pass for one. A non-table `mcp_servers` is refused with the parser's
reason, and `companions add` appends TOML only when the result still parses.

Three states, reported separately because the remedies differ: **registered**,
**installed but not wired up** (one command away), **not installed** (with the command
and the URL). `ddflow adopt` prints the same summary, so the gap is visible at
adoption rather than discovered six tasks later. Exit 2 when a default companion is
missing — "no data", never collapsed into "no problem".

A server is found by its launch as well as its name. A project that registered
codeguide-mcp as `coding-guides` before ddflow knew it reads as
``registered for: claude (as `coding-guides`)``, and `companions add` leaves it alone
instead of writing a second copy under the id. "The same launch" is the same command
(by basename) with the companion's arguments in order — only flags, and the value of a flag known to take one
(`-e TOKEN`) may sit between them, the server's own arguments may follow, and a version
tag on an npm package (`@latest`, `@1.2.3`) is ignored. Another image, package or
launcher is another server, and so is reordered arguments.

`rules` never shows as uncovered: `ddflow brief` serves it from ddflow's own lessons
and operational memory (`ddflow memory add|list|forget`), which is the job the memory
companions below were once recommended for.

| | Serves | Why |
|---|---|---|
| **roborev** *(cli)* | `standards`, `bug_hunt`, `dedupe` | Cross-file duplication analysis, which is the failure mode of agent-written code specifically: an agent changing replicated logic reliably updates one copy and misses the rest |
| **codeguide** | `standards` | Checks against a written standard instead of the reviewer's taste |
| **context7** | `research`, `standards` | A model's memory of a library's API is exactly the kind of claim that is cheap to check and often wrong |
| **memory** *(opt-in)* | `rules` | A machine-local knowledge graph, for a project that wants one. ddflow already keeps operational facts itself (`ddflow memory`), so this is a second store outside the committed log |
| **sequential** | `research`, `rubber_duck`, `bug_hunt` | The three gates that are *reasoning*, not tool-running. A thought can be marked a revision or a branch instead of being appended to a transcript that only grows — so a retracted hypothesis reads as retracted, and what a bug hunt **ruled out** stays visible |
| **optmem** *(cli, opt-in)* | `rules` | Superseded by `ddflow memory`, which holds the operational facts OptMem was recommended for; listed for a project that wants an OptMem store anyway. An existing OptMem `LOG.txt` imports as ddflow memories |

**Servers and command-line tools are different things**, and the registry says which:
`kind = "mcp"` is registrable into an agent's config, `kind = "cli"` is a tool the agent
shells out to. OptMem and pre-commit are examples — real tools with no MCP mode, so
`companions add` refuses them and says why instead of writing a launch entry that would
fail its first handshake. A `cli` companion counts toward its gate's coverage once it is
**installed**; `registered` is a state it cannot reach.

**Your stack needs servers this registry cannot know about.** `ddflow prompts show
research-companions` walks an agent from the pipeline's *uncovered* gates, through the
repository's actual manifests, to candidates checked against their primary sources —
provenance, maintenance, what they execute, what credential they want — and produces
`[[companion]]` blocks you can read and delete. It proposes; you install. A rejection is
part of its report, so the next session does not re-research it.

**Registering is previewable.** `ddflow companions add --dry-run` (and
`ddflow_companions_add` with `dry_run=true`) reports the exact config entry it would
write and writes nothing — not the file, not even its parent directory. The handshake
tells an agent to dry-run first and show the operator the actual entry rather than a
description of it, because registering changes which processes their agent launches.
The preview is asserted to match what the real write produces; a preview that drifts
from the write is worse than none, since the operator has now signed off on it.

**ddflow never installs anything itself** — running an install command on someone's
machine is the operator's decision. What it does instead is *instruct the agent to ask*:
the MCP instruction block lists each missing companion with the gates it serves and the
exact command that would install it, and tells the agent to put that to the operator
early, install it if they agree, and record the affected gates `unavailable` if they
decline. Never on its own word.

`companions add` also refuses to register a server that is not present: that writes a
launch command which fails mid-task, at the moment a gate told the agent to reach for
it. Detection is read-only and bounded — and when it has not run, the state is reported
as **unknown**, not as absent. `ddflow companions` probes; the MCP handshake does not,
because making an agent wait on `npx` before it can do anything is the wrong trade.

Adding a fifth is a TOML block in `.ddflow/companions.toml`, not a patch:

```toml
[[companion]]
id      = "my-linter"
title   = "House linter"
gates   = ["standards"]
detect  = ["my-linter", "--version"]
command = "my-linter"
args    = ["mcp"]
install = "cargo install my-linter"
```

## Wiring it into your agent

`ddflow adopt --agents claude,cursor,codex` writes everything below. This table is what
it writes, so you can check it or do it by hand.

| Agent | `--agents` | MCP config it writes | Rules |
|---|---|---|---|
| **Claude Code** | `claude` | `.mcp.json` | `CLAUDE.md` + `AGENTS.md` |
| **Gemini CLI** | `gemini` | `.gemini/settings.json` | `AGENTS.md` |
| **Codex CLI** | `codex` | `.codex/config.toml` | `AGENTS.md` |
| **GitHub Copilot (CLI + cloud)** | `copilot` | `.github/mcp.json` | `AGENTS.md` |
| **VS Code (any agent)** | `vscode` | `.vscode/mcp.json` | `AGENTS.md` |
| **Kilo Code / Roo** | `kilo` | `.kilo/kilo.json` | `AGENTS.md` |
| **Cursor** | `cursor` | `.cursor/mcp.json` | `.cursor/rules/ddflow.mdc` *+ `AGENTS.md`* |
| **Kimi Code CLI** | `kimi` | `.kimi-code/mcp.json` | `AGENTS.md` |
| **opencode** | `opencode` | `opencode.json` | `AGENTS.md` |
| **ZCode (GLM / Zhipu)** | `glm` | `.zcode/config.json` | `AGENTS.md` |
| **Qwen Code CLI** | `qwen` | `.qwen/settings.json` | `AGENTS.md` + pointer in `QWEN.md` |
| **Google Antigravity** | `antigravity` | `.agents/mcp_config.json` | `AGENTS.md` |
| **Devin CLI** | `devin` | `.devin/mcp_config.json` | `AGENTS.md` |
| **Qodo Command** | `qodo` | `mcp.json` | `AGENTS.md` |
| **Tabnine** | `tabnine` | `.tabnine/agent/settings.json` | `AGENTS.md` + pointer in `.tabnine/guidelines/` |

**7 more are supported with no MCP file to write** — a *verified* absence, not an
unresearched gap. `adopt` writes the delta doc and the `AGENTS.md` block and names the one
manual step. Inventing a path would be worse: ddflow would write a file the agent never
reads, and you would believe it was wired up.

| Agent | `--agents` | Add the server here by hand | Rules |
|---|---|---|---|
| **Aider** | `aider` | no MCP client support at all — drive it from the CLI | `AGENTS.md`, loaded via `read:` in `.aider.conf.yml` |
| **Cline** | `cline` | global settings only; add via its MCP Servers panel | `AGENTS.md` + pointer in `.clinerules/` |
| **Windsurf / Cascade** | `windsurf` | global `~/.config/devin/mcp_config.json` | `AGENTS.md` |
| **Replit Agent** | `replit` | web UI only, remote servers by URL — use the CLI here | `AGENTS.md` + pointer in `replit.md` |
| **OpenHands** | `openhands` | *Settings → MCP* (its `config.toml` form is dev-only) | `AGENTS.md` |
| **Goose** | `goose` | user YAML `~/.config/goose/config.yaml`, under `extensions:` | `AGENTS.md` |
| **Sourcegraph Cody** | `cody` | the editor's `settings.json`, key `cody.mcpServers` | `AGENTS.md`  ⚠ its own convention is undocumented |

### One set of rules, every agent

`AGENTS.md` is the cross-agent convention and most of the 22 read it. **Seven do not read
it first, or at all**, so `adopt` writes the same managed block into their own surface too:

| Agent | Its own surface | Why `AGENTS.md` alone is not enough |
|---|---|---|
| Cursor | `.cursor/rules/ddflow.mdc` | project rules **outrank** `AGENTS.md` |
| Qwen Code | `QWEN.md` | `QWEN.md` is its DEFAULT context file |
| Cline | `.clinerules/ddflow.md` | reads `.clinerules/`, not `AGENTS.md` |
| Tabnine | `.tabnine/guidelines/ddflow.md` | reads `.tabnine/guidelines/*.md` |
| Replit | `replit.md` | its own root-level convention |
| Goose | `.goosehints` | `CONTEXT_FILE_NAMES` is configurable |
| Aider | `.aider.conf.yml` `read:` | discovers **nothing** automatically |

**The rules are inlined, not pointed at.** A one-line "see `AGENTS.md`" stub was the
obvious design, and ddflow's own notes had already refuted it: *a link is only followed if
the agent chooses to follow it*. A rule that binds only when the model feels like opening a
file is not an enforced rule.

That means several copies of one text, and the answer is that **a check owns them**: one
generator, a managed `DDFLOW:BEGIN`/`END` block in each, and `ddflow doctor` comparing every
copy against the generator. Five kinds of break are reported and each fails `doctor`:

```console
$ ddflow doctor
note:    QWEN.md's ddflow section is from an older version and has drifted
note:    .clinerules/ddflow.md exists but its ddflow section was removed
PROBLEM: .goosehints does not exist — the agent has no project rules at all
PROBLEM: .cursor/rules/ddflow.mdc exists but does not bind: `alwaysApply` is not
         true, so the agent may never load it
PROBLEM: .aider.conf.yml exists but does not bind: it does not list `AGENTS.md`
         under `read:`, and Aider loads no instruction file it was not told to load
```

Files the project already owns — `QWEN.md`, `replit.md`, `.goosehints` — get a block
**merged into** them; your own content stays. Aider's `read:` list is extended, not
replaced. Adopting is idempotent: re-running never appends a second block.

Every other agent reads `AGENTS.md` directly, which is the point of it being canonical.

Cursor gets its own rules file because **its precedence puts project rules above
`AGENTS.md`** — writing only `AGENTS.md` there would be writing to a file the agent
outranks. `adopt` merges into these files rather than overwriting: they hold your other
servers and your other rules, and a tool that stomps them is a tool you run once.

The MCP entry is one line in any of them:

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

`uvx` fetches and runs it in an ephemeral environment on first use — no clone, no
`PYTHONPATH`, no install step to forget. Prefer Docker? `docker run -i --rm -v
"$PWD:/repo" ghcr.io/delian/ddflow-mcp`, which needs the repo bind-mounted because ddflow
operates on your actual git checkout.

**Standalone, with no MCP at all**, is a first-class mode rather than a fallback. Add to
`AGENTS.md` / `CLAUDE.md`:

```markdown
This project's work is a queue managed by ddflow. Before doing anything, run
`ddflow brief`. Claim before you edit (`ddflow claim <id>`), satisfy every gate
(`ddflow gate status <id>`), then `ddflow merge` and `ddflow complete`.
Never pass a gate you did not perform — record `unavailable` with the reason instead.
```

That is the whole integration. An agent with nothing but a shell can drive the entire
workflow, which is why MCP is a convenience layer here and never a requirement.

---

## From plan mode to the queue

Agents plan well and forget reliably. A plan that lives in a chat transcript is gone at
the next session; a plan in the queue survives, fans out to parallel agents, and carries
its own gates.

Tell the agent, at the end of planning:

```
Put that plan in ddflow before you build any of it. One phase for the whole plan, one
task per independently-shippable step. Declare each task's globs — the files it will
write — and its needs, the tasks that must finish first. Then show me `ddflow next`.
```

What the agent does with that:

```console
$ ddflow phase add P3 --title "Rate limiting"
$ ddflow task add P3.T1 --phase P3 --globs 'limiter/**'      --title "token bucket"
$ ddflow task add P3.T2 --phase P3 --globs 'api/middleware/**' \
      --needs P3.T1 --title "wire it into the request path"
$ ddflow task add P3.T3 --phase P3 --globs 'docs/**' --needs P3.T2 --title "document it"
$ ddflow next
Ready (1 ready, 0 running, 2 blocked):
  P3.T1  token bucket
      writes: limiter/**
  (blocked) P3.T2: deps — P3.T1 is open
  (blocked) P3.T3: deps — P3.T2 is open
```

**The two fields that do the work are `--globs` and `--needs`.** Globs are how two agents
are stopped from editing the same file: `claim` refuses an item whose writes overlap one
already held, and names what to take instead. Needs are how ordering is enforced without
anyone remembering it. A plan whose tasks declare neither is a list, not a queue — it
will *look* parallel and then two agents will fight over one file.

`ddflow split <id> --into a,b,c` exists for when a task turns out to be three, which is
the normal case rather than a failure of planning.

---

## When a companion is missing

ddflow imposes the order and demands the evidence. It does not *perform* the judgement
inside most gates — that is what the companion tools are for. So the honest question is
what happens when one is absent, and the answer is deliberately never "the gate passes".

| Companion | Serves | If it is missing |
|---|---|---|
| **roborev** *(cli)* | `standards`, `bug_hunt`, `dedupe` | Record the gate `unavailable` with the reason. A second opinion is missing and the log says so. |
| **codeguide** | `standards` | The standards gate falls back to the reviewer's taste. Still recordable — but say which it was. |
| **context7** | `research`, `standards` | Claims about a library's API rest on the model's memory, which is exactly the claim that is cheap to check and often wrong. |
| **sequential-thinking** | `research`, `rubber_duck`, `bug_hunt` | A retracted hypothesis becomes one more assertion in a linear transcript, and what you ruled out disappears. |
| **memory** / **OptMem** *(opt-in)* | `rules` | Nothing: `ddflow memory` keeps the facts about *this machine*, `brief` shows them and `recall` searches them beside the decisions, lessons, research and bugs. |

**The rule, and it is enforced:** a gate whose tool could not run is recorded
`unavailable` with the reason, never `passed`. `ddflow complete` reports those as a
**coverage gap** on the completion event, so a finished item never silently implies that
a check happened. Set `[gates].unavailable_is_failure = true` and a gap blocks completion
outright.

`ddflow companions` reports four states, and the difference between the last two is the
whole point: **registered**, **installed but not wired up** (one command away),
**missing** (with the install command and the URL), and **not checked** — because the
MCP handshake does not probe, and *"nobody looked"* must never render as *"not there"*.

**ddflow never installs anything.** Detection is read-only and the report is advice.
`ddflow companions add --dry-run` shows the exact config entry it *would* write, so an
agent can show you the change before making it.

---

## Adopting a project that already has history

A queue that starts empty tells the next agent "nothing is in flight" about a repository
with three branches in flight and forty open items in a todo file — and the agent
believes it, because the tool said so. That is worse than having no tool at all.

```console
$ ddflow import                      # looks; writes nothing
What this project already has (nothing written yet):

  314 phase(s):
    [ ] 142.A     the scaling-law advisor is wrong (P0; CONFIRMED)   docs/todo.md:26517
  1170 task(s):
  442 lesson(s):
  ...
  47 memory(s):
    [ ] M-0002    Hardware: 8x H200 GPUs on this box, usually idle.  .agent_memory/LOG.txt:3

  NOTE: 3631 already-ticked task(s) were NOT imported. They are history, not a queue.
  NOTE: 32 phase heading(s) say the work is finished while their checkboxes are still
        unticked: 99 (4 open), 103 (3 open), ... Ask the operator which is stale.

$ ddflow import --apply              # writes them, each recording its source line
```

Seven sources, all optional, all in the places projects actually keep them:

| Source | Read from | Becomes |
|---|---|---|
| Todo checklists | `docs/todo.md`, `docs/todo/open/*.md`, `tasks/todo.md`, `TODO.md`, `docs/plan.md`, `ROADMAP.md` | phases and tasks, with declared `Needs:`/`Globs:` |
| Lessons | `docs/lessons.md`, `LESSONS.md`, `docs/retrospectives/*.md` | lessons, searchable by `ddflow recall` |
| Decisions | `docs/adr/*.md`, `docs/decisions/*.md` | decisions, `Superseded` preserved as superseded |
| Research | `docs/RESEARCH.md` | research notes, `CONFIRMED`/`REFUTED`/`THEORETICAL` carried across |
| Journal | `docs/log/*.md`, `CHANGELOG.md`, `docs/journal/*.md` | session notes, dated by **when they happened** |
| Cross-session memory | `.agent_memory/LOG.txt` (OptMem), `.memo/`, `.optmem/` | session notes, with each record's own date |
| In-flight work | branches with commits not on the base | tasks, named with how far ahead they are |

### What it will and will not decide for you

**Mechanical, and verifiable:** a ticked checkbox is a fact, a `##` heading is a
section, a branch with unmerged commits is work. The id in `### 142.A — …` or
`- [ ] **WFOPT.4.6** — …` is read, not invented, so the imported queue uses the ids the
project has been writing in commit trailers for months.

**Judgement, and yours:** which open items are actually live, what each task writes,
what depends on what. The `/import-existing-project` prompt walks an agent through that
with the operator. It is not automatable, and a confident guess produces a wrong queue
the scheduler then hands out.

Four guard rails, each of which exists because the alternative is silent:

- **Dry run by default.** `--apply` writes. Looking is free and never a side effect.
- **Finished work stays out** — it is history, not a queue — *except* a completed item
  that open work depends on, which comes along as done so the open item is not stranded
  on an id the queue has never heard of. A needed *phase* is judged over every task under
  it, so a ticked task pulled in as a dependency does not keep the phase open, and a plain
  import completes a finished phase that open work needs (re-running repairs one left empty).
- **`[importer] max_tasks` (default 200) refuses a whole history.** An import writes
  events into a log that is committed to git; one real repository yielded 4,799
  checkboxes. Over the cap it proposes none and says so — the phases are withheld with
  them, because a queue of empty phases is not a smaller import, it is a misleading one.
- **Idempotent.** Ids derive from the source, so re-running after you edit the todo adds
  what is new and leaves the rest alone. A second run over an unchanged project exits 2.

### What an unticked box means, and where things live

An open box is not always work. The import reads the project's own **dispositions** —
the vocabulary and positions are those of the picker ddflow was extracted from, and
agree with it on 1,166 of that repository's 1,170 open boxes. Of the four, two are the
picker's own false positives ("cells run / skipped" in plain prose) and two are recorded
non-findings with an unclosed "(… refuted it" aside, which ddflow closes:

| The source says | Imported as |
|---|---|
| `DEFERRED`, `THEORETICAL`, `BLOCKED`, `ON HOLD`… after the title or in a `(aside)`; a `### Deferred` heading; `**STATUS**: DEFERRED` / `WATCH` | **blocked**, with the reason and the source line. Never offered; `ddflow unblock <id>` releases it |
| `DECLINED`, `REFUTED`, `SUPERSEDED`, `SKIPPED`, `~~struck through~~`; `**STATUS**: SHIPPED` / `CLOSED` over an unticked box | history, like a ticked box — left out, or **abandoned** with `--include-done` |
| a word in the title's own prose (`make the sampler handle SKIPPED batches`) | work — that is the item that fixes it. With no bold title, the title is the first sentence before a dash; a later sentence ("Out of scope for v1.") or a `MARKER:` lead is annotation |

`[importer] archive_globs` names plan files that are history until a section is named
(a 20,000-line legacy `docs/todo.md`): their open boxes import **blocked**, and
`ddflow unblock <phase>` releases a whole section at once.

Each source family's location is a knob — `todo_globs`, `lesson_globs`,
`lesson_summary_globs`, `decision_globs`, `research_globs`, `journal_globs`,
`memory_globs` — and a set knob **replaces** the defaults, because the same filename
means opposite things in different projects (`docs/LOG.md` is one repository's whole
journal and another's generated index of it; an `Index` section is never imported).

Lessons are split at the level they actually live at: `### L100. …` entries grouped under
`## <date>` headings import one per lesson **with their own ids**, so `[L147]`
cross-references still resolve — and so do hyphenated ids (`L-12`, `R-7`), kept as ids in
headings and citations alike. A lesson's `**Compressed:**` paragraph becomes its
**summary**; a hand-written `lessons-summary.md` bullet that cites exactly one lesson
becomes that lesson's summary, and every other bullet becomes a consolidated lesson tagged
`summary`. A GENERATED summary file is skipped. `ddflow render` writes them all back out
as `docs/ddflow/LESSONS-SUMMARY.md` (also `ddflow://lessons-summary`).

**An import adds nothing twice, and says what repeats.** Lessons, decisions and research
are weighed against every record already in the log (and a summary bullet against the
lessons of the same import) with the same similarity engine and `[dedupe]` thresholds an
add uses. Text identical to a held record is left out and counted; a record scoring at
least `[dedupe].ask_threshold` (0.55, with `min_words` content words) is left out and
LISTED in the plan's notes as `lesson LS-git-rebase ~ L12 (0.74)  docs/lessons-summary.md:9`
-- the candidate id and score -- so the operator or onboarding agent decides: nothing is
written for it, and filing one anyway is `ddflow lesson add` (or `decision add` /
`research add`) once the existing record is judged not to say it. No score separates a
duplicate from a related record, which is why it is reported rather than decided. On a
real project's lessons-summary, 68 of 86 bullets that restate a corpus lesson were
reported this way. Tasks and phases are not checked (they carry dependencies);
`[dedupe].on_match = "warn"` reports the same list but imports them anyway, and
`"off"` turns the check off. Re-running over the same files adds
nothing.

### Verifying an import, at any time

The import's weak spot was never the parsing. It is everything *after* `--apply`: 1,170
tasks arrived in the real-corpus run, and the workflow prompt tells an agent to give each
one globs and declare its dependencies. Nothing checked whether that ever happened — and
an imported queue nobody finished misrepresents the project exactly as an empty one does,
believed harder because a tool produced it.

```console
$ ddflow import --verify
Imported between 2026-09-25 and 2026-09-25:

       4 decision(s)
    1727 journal(s)
     442 lesson(s)
      47 memory(s)
     314 phase(s)
      72 research(s)
    1170 task(s)

Left to decide or fix:
  - 1078 imported task(s) declare no globs, so the conflict detector cannot protect
    them and two agents can be handed the same file: OPIK.1b, OPIK.2, ...
  - 27 phase(s) say the work is finished while a task under them is still open: 99,
    103, 115.D.2, ... Ask the operator which is stale before anyone claims from them.
```

Three answers, three exit codes, because collapsing them loses the one that matters:

| Exit | Meaning |
|---|---|
| `0` | imported, still matches the sources, and every imported task says what it writes |
| `1` | imported — and here is what a human still has to decide |
| `2` | nothing was ever imported. An answer, not a failure |

It reports **status** (what is imported, per kind, and when), **whether it is still
true** (what a re-run would add, which sources yielded nothing, which source files have
since vanished), and **whether anyone finished it** (tasks with no globs; phases whose
heading claims SHIPPED over an open task).

It deliberately does **not** repeat `ddflow doctor`, which already reports unresolved
dependencies, duplicate globs and cycles. Two commands reporting one defect in different
words is how an operator learns to read neither.

Provenance is a **field**, not prose. `Item.source` is `docs/todo.md:41`; the body still
says *"Imported from docs/todo.md:41."* for a human reading `ddflow show`. Answering
"which items came from the import" by regexing that sentence would mean the day someone
rewords it, the count silently becomes zero and the verification passes.

**The connection handshake follows through.** The offer to import stops once the queue
has anything in it — but if imported work is still missing globs, or a phase still claims
SHIPPED over open tasks, the MCP instructions say so and tell the agent to run
`ddflow_import_verify` before handing any of it out. That check is computed from the
already-folded queue, so it costs nothing; the source re-scan (~0.65 s) stays out of
every session start and happens only when someone asks for it.

**Re-running is a first-class path.** `/import-existing-project` opens by checking what
is already imported and switches to *finishing and refreshing* rather than repeating —
fix the globs it names, ask the operator about the SHIPPED drift, re-run `ddflow import`
for sections added since.

### What it reports rather than fixes

Three kinds of drift it can see and must not resolve on its own, because either answer
could be the wrong one:

- **A phase heading that says `SHIPPED` over unticked checkboxes** (32 of them in the
  repository this was measured against). One-sided risk: if the heading is right, the
  queue is about to hand out work that is already done.
- **A dependency on an id nothing produced.** Kept and treated as unmet — deliberately,
  so a typo surfaces as blocked work rather than as work that starts early — but named,
  because "never offered" otherwise looks exactly like "nobody has got to it yet".
- **A file that matched a source pattern and yielded nothing**, which usually means an
  unusual format rather than an empty file.

---

## The model: phases, tasks, dependencies, globs

```
Plan ──► Phase ──► Task
```

A **phase** is a unit of *review*: its own research, its own whole-suite test pass, its
own live smoke run, merged as one coherent feature. A **task** is a unit of *execution*:
one agent, one worktree, one pipeline, one merge. Both carry `needs` (dependencies, which
may cross phases) and `globs` (the files they will write).

```sh
ddflow phase add P2 --title "Billing" --needs P1
ddflow task add P2.T1 --phase P2 --title "invoice model"  --globs "src/billing/invoice.py"
ddflow task add P2.T2 --phase P2 --title "tax rules"      --globs "src/billing/tax.py"
ddflow task add P2.T3 --phase P2 --title "checkout wiring" --needs "P2.T1,P2.T2" \
                                                            --globs "src/checkout/*"
```

**Declare globs.** They are what lets two agents work at once safely. A task with no
declared globs is a task the conflict detector cannot protect.

**Dependencies are inherited.** A phase is never claimed — only its tasks are — so
`P2 needs P1` has to govern everything *inside* P2, or it governs nothing that anyone
picks up. The readiness rule therefore consults an item's ancestors as well as itself:

```console
$ ddflow next
Ready (1 ready, 0 running, 1 blocked):
  P1.T1  money
  (blocked) P2.T1: deps — phase P1 has 3 open task(s) (inherited from P2)
```

The refusal names *where* the dependency came from, because an operator told only
"P2.T1 needs P1" goes looking for a declaration that is not written there. The one
dependency **not** inherited is one pointing into your own subtree: an umbrella that
declares a dependency on its own child would otherwise make the child wait for itself,
turning a plan typo into a permanent hang.

`ddflow claim` asks the *same* predicate `ddflow next` does. They used to disagree —
`next` withheld a task on its dependencies and `claim` handed out a worktree for it a
second later — so an agent picking work by id rather than by asking bypassed the
dependency graph entirely.

There is deliberately no third level *of kind*: a sub-task is a task whose parent is a
task, so depth is unlimited while the rules stay one set.

---

## Work that changes shape while you do it

Tasks can be added at any time, including while their parent is being worked — mid-task
discovery is the normal case, not an exception, and a queue that cannot absorb it pushes
the work into someone's head.

**Sub-tasks are just tasks whose parent is a task.** Not a separate concept with its own
rules: a sub-task declares its own globs, carries its own dependencies, is claimed by its
own agent, and runs in parallel with its siblings when nothing links them — exactly like
any other task.

```sh
ddflow task add P1.T1a --parent P1.T1 --globs "src/parse.py"
ddflow split P1.T1 --into "P1.T1a=parse input" --into "P1.T1b=write records"
```

`split` works **in place**: the original keeps its id, its lease history and everything
recorded against it, and becomes an *umbrella* that completes when its children do.
Closing it and opening two new ones instead would lose the thread between what was
planned and what happened — which is exactly what `ddflow replay` needs.

An umbrella is never offered as ready (its children are), and cannot complete while any
descendant at any depth is unfinished. An *abandoned* child counts as settled, so a
sub-task you decide against does not hold its parent open forever.

**Becoming an umbrella releases the lease**, however you get there — by `split`, or by
adding the first sub-task to a task you are already working. An umbrella holding a live
claim on globs that overlap every child's means a second agent cannot take one of those
children, and crash recovery points at a worktree where nothing further will happen.
`split` already did this; `task add --parent` did not, which is the shape of bug worth
naming: one transition, two ways in, guarded on one.

## Architectural decisions

The code shows *what* was built and never *why*, nor what was rejected on the way. So
decisions are recorded as events, and reach the person writing the code:

```sh
ddflow decision add --title "Storage is SQLite with WAL" \
  --decision "One file, WAL mode, BEGIN IMMEDIATE for writes." \
  --context "Three call sites were each opening their own connection." \
  --alternatives "Postgres — rejected: no server allowed in this deployment." \
  --globs "src/storage/*" --by operator
```

**`--globs` is what makes a decision consulted rather than merely filed.** `ddflow
brief` and `ddflow decision applicable <item>` surface the decisions governing an
item's declared files automatically — the agent does not have to suspect they exist.

Decisions are never edited or deleted. A reversal is a *new* decision naming the old
one (`--supersedes`), so the history of how the architecture got here survives, and a
superseded decision is shown with a pointer to its replacement rather than silently
withheld.

## Recall — "have we been here before?"

```sh
ddflow recall "how should durations be represented"
```

One search across **everything the project remembers**: architectural decisions,
lessons, operational memories, research verdicts, past bugs, similar tasks, and the
operator's own earlier prompts. Results are labelled by kind, because a binding decision, a transferable lesson
and a prompt from three weeks ago should change what you do in different ways.

It exists so the operator does not have to say the same thing twice and the agent does
not have to learn the same thing twice. Both failures are invisible in the moment and
obvious in the log.

**Which existing record is this new one like?** The similarity engine answers that without
an LLM: a code-aware tokenizer (identifiers split on case and underscores, a light
stemmer), TF-IDF weights projected into `index.db` by `ddflow rebuild`, and an exact cosine
over an inverted index — a record missing from the hits shares no term with the query, so
nothing depends on how SQLite was built. Candidates cross kinds, so a new bug is shown the
open task that fixes it. Its policy is the `[dedupe]` section (decision D-no-duplicates):
`on_match` (`ask` default, `warn`, `off`), `show_floor` (0.35) and `ask_threshold` (0.55)
on the cosine, `max_candidates` (3), `min_words` (8) and `kinds`. The thresholds come from a
labelled set of 84 duplicate / related / hard-negative pairs built from real logs
(`tests/fixtures/dedupe/`), which the engine must keep meeting; no score separates a
duplicate from a different bug in the same function, which is why the default asks rather
than decides.

### Similar — "is this already filed?"

```sh
ddflow similar "claim refuses a worktree that already exists"
ddflow similar "claim refuses a worktree that already exists" --kind task --json
```

Before you file a bug, task or lesson, ask what the log already holds that reads like
it. `ddflow similar` (MCP: `ddflow_similar`) is read-only and takes the text you would
file, and optionally `--kind` (a comma-separated subset of `[dedupe].kinds`; anything else
is refused, exit 1). Each candidate shows its id, kind, title, **state** (open, claimed by
whom, done, fixed, invalid...), **score** (0-1, TF-IDF cosine), the words it
shares with your text, and flags: `identical` (the same text up to case and whitespace)
and `named` (your text names its id). Candidates cross kinds, so a bug sees the open task
that fixes it and a task the bug it would fix, and closed records are included: a new bug
that repeats a fixed one is exactly the case worth catching.

Exit codes: **0** with candidates, **2** with none. `--json` (and the MCP tool) return
the same list of candidates, and an empty list when there are none. It uses the
`[dedupe]` knobs `show_floor` (lowest score listed, default 0.35), `max_candidates`
(default 3; records whose id the text names are listed as well) and `kinds`; it runs even
when `[dedupe].on_match` is `off`, since that setting governs what an *add* does. A score
is a prompt to look, not a verdict: two bugs in one function score high and are different.

## Operational memory

```sh
ddflow memory add "8x H200 on this box; check nvidia-smi before a GPU test" --tags gpu
ddflow memory list                       # newest first; --query to rank, --all for forgotten
ddflow memory forget M-0003 --reason "the box was upgraded"
```

One **fact** about this machine, repository or working state — not a rule (`lesson`),
not what happened (`session note`), not how the software is built (`decision`). Capped
at `[memory] max_chars` (280) and refused, not truncated, when longer. The newest
`[memory] brief_items` appear in every `ddflow brief`, right after the binding decisions,
and `recall` searches them — the job an OptMem store beside the repository used to do,
now in the log, so every worktree sees a memory the moment it is written. A memory that
stopped being true is **forgotten with a reason**, never deleted: "we thought X until Y"
is what stops the next agent re-learning X. An OptMem `LOG.txt` imports as memories
dated when they became true. The log is committed: never put a secret in one.

## Resources and long-running jobs

Globs keep two agents out of one file. Work that **runs** on something — GPUs, a model
server, a shared fleet — declares that too, and the same refusal applies:

```sh
ddflow update TRAIN.3 --resources gpu:6            # or **Resources:** gpu:6 in the plan
ddflow claim TRAIN.3                                 # refused (exit 3) if it does not fit
```

`[schedule] resources = ["gpu=8", "vllm-fleet=1"]` sets capacities; a resource named
nowhere is exclusive. Every live claim counts, the claimant's own included — one agent
starting two 8-GPU runs overcommits the box just the same — and `next` withholds what
does not fit, saying who holds what.

The run itself is a **job**:

```sh
ddflow job run TRAIN.3 "uv run main.py train -c cfg.toml"   # detached; survives you
ddflow job list        # running | exited N (from its log) | gone (killed) | elsewhere
ddflow job end J3f2 --note "loss 0.12, ckpt in out/"         # refused while it runs
```

A launched job runs in the item's worktree, in a session of its own (it outlives the
agent, the MCP server and a restarted remote-control service), and appends its exit code
to its log so a run nobody watched still says how it ended. Liveness is computed, not
stored: a zombie is not alive, and a reused pid is caught by the process start time.
`ddflow job add --pid` registers a process started some other way. Every `brief` lists
jobs not yet recorded as ended — "WAIT, do not start it again" for a running one.

## Dependencies on another repository

```toml
[schedule]
repos = ["run_nemo_run=../run_nemo_run"]
```

`ddflow update GEN.4 --needs run_nemo_run:132.D` then waits for item `132.D` **there**
to be done. `ddflow external sync` (and every session-start hook) reads the sibling's log
— never writing it — and records what it observed in this one, only when it changed.
Readiness is decided from that dated fact, so the fold stays pure and "why was this
started?" is answerable later. An unobserved external dependency is unmet; one naming
a repository that is not configured is a `doctor` problem, since it can never be met.

## Status, progress, and loops

```sh
ddflow status      # what is done, in flight, ready, blocked — one answer
ddflow progress    # attempts, hours held, gate runs, commits, per item
ddflow loops       # circular references and runtime loops (exit 2 = none)
```

`progress` counts a landing once (`commits` holds distinct shas). `ddflow show <id>` takes a
**bug** id too: its state, where it was found, the fix task(s) — a task that says "fixes
bug X" is a fix, a mention is not — the regression tests it was closed with, and an
invalid closure a later fix superseded. `ddflow --json board` prints the board as JSON;
`--agent`, `--repo` and `--json` are accepted after the subcommand as well as before it.

`next --phase`, `brief --phase` and `board --phase` refuse an id that is not an item
and `next` names the phases that start with it; `board --phase` given a task id refuses,
naming that task's phase. `ddflow status` over MCP is **bounded**: the counts are exact, but each
long list is cut to the 25 most recent (the first 25 in scheduler order for the others),
with a `truncated` note naming the real lengths; the CLI, and `ddflow --json status`,
list everything. `doctor` and `status` also say when the log holds events from a **newer
ddflow** than this checkout runs — they were skipped, so the numbers are computed without
them — and the remedy is to merge main or run the newer ddflow.

**`brief` says whose work it is.** Run under an identity, its `## Current` is the agent's
own most recent lease (several are listed, with `--item <id>` for another); an agent
holding nothing sees `## Suggested next` — the queue's top ready item, to `claim` first —
because calling it Current told an agent it was working on something it never claimed.
`complete` likewise defaults the author model from the agent's open `session start`.

Dependency cycles are the easy case. The expensive ones are *runtime* loops, where the
graph is perfectly acyclic and the work still never finishes:

| Detector | Catches |
|---|---|
| `dependency_cycle` | A needs B needs C needs A — always blocking |
| `repeat_claims` | claimed and given up N times without completing (crash-expiries excluded: that is a different problem) |
| `gate_flapping` | a gate whose verdict keeps flipping — flaky, or measuring a moving target |
| `reopened` | work that will not stay done, usually because the acceptance criteria are not in the item |
| `duplicate_work` | two live items declaring the same files |
| `no_progress` | N recent events with no completion, no gate pass, no merge |

Every threshold is a `[loops]` knob, and `on_detect = "block"` makes `ddflow claim`
**refuse** an item that is already looping — a warning is read by a human later, a
refused claim is read by the agent now.

## The task pipeline

Ten gates, in order, configurable per project:

| # | Gate | Run by | Purpose |
|---|---|---|---|
| 1 | `research` | agent | State a falsifiable claim; probe it before building on it |
| 2 | `rules` | agent | Load project rules + the lessons relevant to *this* task |
| 3 | `implement` | agent | Write the change, in its own worktree |
| 4 | `rubber_duck` | **different-family** model | Try to *refute* the change |
| 5 | `critic` | **different-family** critic | Where does the diff disagree with the intent? |
| 6 | `standards` | tooling | Linters, architecture review, coding-standards MCP |
| 7 | `unit_tests` | tooling | The project's suite, actually executed |
| 8 | `bug_hunt` | agent | Hunt the recurring classes across everything touched |
| 9 | `dedupe` | agent | Did this re-implement something already present? |
| 10 | `merge` | ddflow | Land it, from the primary checkout, with no checkout |

Four things are enforced rather than requested:

**Silence is not a pass.** Every gate in the pipeline must carry *some* outcome before
an item completes — passed, failed, unavailable, partial, or an explicit
`ddflow gate skip <id> <gate> --reason "..."`. Without this, `gates.required` held only
`implement`, `unit_tests` and `merge`, so six of the ten steps could be omitted with no
trace at all. `gates.require_outcome = false` makes the pipeline advisory again;
`gates.enforce_order` ("warn" by default, or "block") reports a gate recorded before an
earlier one has run, because a rubber-duck review recorded before `implement` reviewed
an empty diff.

**UNAVAILABLE is never a pass.** A reviewer whose endpoint was down approved nothing; a
linter that is not installed found nothing. Each gets its own outcome and shows as a
coverage gap. (The inverse matters too: this codebase's first version classified a
*missing binary* — shell exit 127 — as `failed`, so an uninstalled linter looked like a
linter reporting problems. Fixed, with a mutation-verified regression test.)

**Evidence or it did not happen.** Gates in `gates.evidence_required` reject a bare pass;
they want the command, its exit code and its output digest.

**And evidence says WHICH tree and HOW MUCH.** Every gate that produces an OUTCOME —
command gates, and agent gates recorded with `gate record` — carries a
`tree_sha`: a fingerprint of the working tree it ran against, covering committed
state, uncommitted changes to tracked files, *and* the content of untracked ones (a new
module is untracked until its first commit, which is the ordinary state of agent work).
ddflow's own `.ddflow/` is excluded, or recording a gate's outcome would invalidate the
gate that just recorded it. If the tree moves afterwards, `complete` warns that the pass
describes source nobody is shipping — a warning, not a block, because refusing on a
comment-sized change is how a check gets switched off. The comparison is of **content
trees**, not commit ids, so an amended message is not a change; once the item has
landed it is made against the commit that landed (a merge commit's second parent, which is
what the gates ran on — never the primary checkout, whose files are everyone's), and the
note names what differs. Evidence that cannot be compared says so rather than passing
silently.

Beside it, `diff_stat` records files, insertions, deletions and untracked count —
including the *lines* in untracked files, because a new module is untracked until its
first commit and a task that is entirely new files would otherwise report zero
insertions. The fingerprint answers *which* tree and is opaque; this answers *how big*,
and that is what makes a pass auditable later — a review gate that passed over 4,000
changed lines in two minutes is a different claim from one that passed over 12.

Neither is recorded for a **skip** (nothing was reviewed, so a magnitude would imply an
inspection that did not happen) nor for an `unavailable` gate that never ran.

Hashing untracked content is capped by `MAX_UNTRACKED_HASHED` (512). Above it the
fingerprint falls back to file *names* and says so inside the digest, because a check
that quietly stopped covering content would go silent for exactly the repositories that
need it most.

**Reviewer independence is checked, and an unidentified reviewer establishes nothing.**
Same-family reviewers share the author's blind spots, so their agreement measures shared
priors rather than correctness. `complete` refuses unless one reviewer came from a
different pretraining family — and a reviewer whose model is not in `[agent].families`
counts as *unknown*, never as *different*. (It used to count as different: `gate record`
defaults the reviewer to the agent id, so a `standards` gate recorded with no `--model`
arrived as family "host-12345", compared unequal to "anthropic", and satisfied the
independence requirement on its own.)

A **router** author is a set of families. Copilot CLI's HydraFusion is chosen like a model
but routes each task across models from several providers, so `[agent].routers` maps its
name to the families it draws on, and a reviewer counts as independent only when its
family is outside the whole set. HydraFusion ships with an empty set — GitHub publishes no
fixed roster — so `complete --model hydrafusion` refuses, naming the knob, until you list
the families your plan routes to:

```toml
[agent]
routers = { hydrafusion = ["anthropic", "openai", "google"] }
```

```console
$ ddflow complete P1.T1 --model claude-opus-5
cannot complete P1.T1 — 1 unmet condition(s):
  - reviewer independence not satisfied: every reviewer (rubber_duck) was family
    'anthropic', the same as the author. Same-family agreement is not independent evidence.
```

Every unmet condition is listed **at once** — a refusal that reveals one problem at a
time trains an agent to reach for `--force`.

---

### Proving a gate can fail at all

```console
$ ddflow gate verify T1 unit_tests
  OK   src/calc.py: detected

unit_tests CAN fail: every registered mutation was caught.
```

A gate that cannot go red is worse than no gate — it reports success on every change,
and everyone downstream reads that as evidence. `gate verify` breaks what the gate
guards, using the `mutations` registered beside it, and requires the gate to notice:

```toml
[gate.unit_tests]
command = "python -m pytest -q"
cwd = "repo"
mutations = [ { file = "src/calc.py", old = "return a + b", new = "return a - b" } ]
```

Four things make it honest rather than ceremonial, and the last one is this feature's
own bug, found by a cross-family review of it:

- **A mutation that did not apply is a FAILURE, not a skip.** If `old` is absent — or
  present twice, so the edit is ambiguous — the check fails. Skipping turns "the
  mutation never happened" into a green run, which reads as the opposite of the truth.
- **The source is restored whatever happens**, including on exception, or a failed
  verification leaves the tree broken and the next gate reports the verifier's fault.
- **A gate with no registered mutations is reported as unproven.** Declaring a check
  nobody has shown can fail is what this exists to catch. An agent gate says plainly
  that it has no command to mutate and rests on its evidence contract instead.
- **A green baseline is required first.** A gate already red for an unrelated reason —
  one pre-existing failing test, a tool that stopped being installed, a flake — reports
  `failed` for every mutation, so every mutation reads as *detected* and the gate is
  certified as able to fail when nothing has shown any such thing. The check written to
  catch the vacuous-pass class contained it. It now runs unmutated first and refuses
  without a pass.

### When the exit code is not the verdict

Some tools say "I could not run" or "I only did part of it" with an exit code, and some
exit 0 whatever happened. A command gate can say which:

```toml
[gate.critic]
command = "uv run scripts/critic_review.py --dirty -c configs/review_critic.toml"
unavailable_exits = [2, 143]      # endpoint down / SIGTERM: UNAVAILABLE, not failed
partial_exits = [3]               # reviewed part of the diff: PARTIAL
require_output = '^STATUS:'       # exit 0 without it = the tool did not do its job
fail_output = '^\s*- \[(HIGH|MEDIUM)\]'   # exit 0 WITH findings = failed
```

`ddflow gate run` also renews the caller's lease every `[lease] heartbeat_s` while the
command runs, so a 25-minute suite does not outlive a 30-minute lease and read as
abandoned work.

It keeps the run's **whole output** in `.ddflow/runs/<item>/<gate>-<time>-<pid>.log` (the
newest ten per item and gate; the directory ignores itself, so nothing is committed) and
records its path beside the digest, so the digest can be checked against bytes that still
exist. The evidence also carries the suite's own verdict lines (`summary`: pytest's
`=== 3 failed, 112 passed ===`, unittest's `FAILED (failures=2)`) lifted from the *whole*
output, because a gate that reruns its failures ends on the rerun's "112 passed" and the
tail alone would hide the first pass's "115 failed". `gate record --output-file` keeps the
file's path the same way.

---

## The phase pipeline

```
research → [ task, task, task … ] → unit_tests → bug_hunt → dedupe
         → live_test → corrections → docs → merge
```

`docs` runs once per phase, before it merges: every user-visible change the phase made
(commands, MCP tools, flags, knobs and defaults, output, setup) is checked against the
README and the docs and updated there, with the files changed as its evidence. The
per-commit stale-docs check catches a renamed or removed name; this catches the new
feature nobody wrote down.

`live_test` is the one most often skipped and the one most worth keeping: **a green unit
suite and a working feature are different claims.** Run the real thing on a small input
and paste what it printed.

---

## Human approval: a gate the agent cannot clear

Every other gate here is satisfied by the agent — it runs a command, or it asserts it
did the thinking. That is right for work whose correctness is checkable afterwards, and
wrong for a **plan**: by the time an agent has built the wrong thing, the cost is
already paid.

A gate marked `human = true` is where the operator says *yes, build that* before the
compute is spent.

```toml
# .ddflow/gates.toml
[gate.plan_approved]
title  = "Operator approves the plan"
human  = true
prompt = "Show the operator what you intend to build, then ask."
```

```console
$ ddflow gate run T1 plan_approved
gate 'plan_approved' is a HUMAN-APPROVAL gate. It is not something you can run or
record — it is where the operator decides whether this work should proceed.
  ddflow approve T1 plan_approved
  ddflow approve T1 plan_approved --reject --reason '...'

$ ddflow gate record T1 plan_approved --outcome passed --evidence "looks fine"
'plan_approved' is a human-approval gate: it is cleared by a person, not by an agent
recording that it happened.                                            # exit 3

$ ddflow approve T1 plan_approved --note "read the plan, ship it"
T1.plan_approved approved by delian — read the plan, ship it
```

**A rejection is a first-class outcome**, not the absence of an approval: *"the operator
looked and said no"* and *"nobody has looked yet"* are different states, and an item
sitting in the second forever is how a checkpoint becomes a silent stall. `--reject`
requires `--reason`.

**There is deliberately no MCP tool for this**, and `tests/test_mcp_parity.py` records
the exemption with that reason. A human checkpoint reachable from the MCP surface is not
a human checkpoint — it is a second `gate record` with a longer name. `gate skip` is
refused too: *"the operator does not need to approve this"* is not the agent's call.

**What this is, precisely.** An audit trail and a speed bump, **not a security
boundary.** An agent with shell access can run `ddflow approve` itself, and no design
here changes that — the tool does not control the machine.

The guarantee, as narrowly as it holds: **no MCP tool records a human outcome**, and a
clearance carries the OS user and a `human` flag, so a forged one is *visible in the
log* rather than indistinguishable from a real one. (`gate.<id>.human` is also refused
by the config writer, because two MCP calls — flip the flag, then record — used to
clear the gate with no shell involved. Declare human gates in `.ddflow/gates.toml`,
which no tool writes.)

Opt-in: the shipped pipeline has no human gate, and a test keeps it that way.

---

## Parallelism and coordination

```console
$ ddflow next --phase P1
Ready (3 ready, 0 running, 1 blocked):
  P1.T1  persistent store
      writes: shortener/store.py, tests/test_store.py
  P1.T2  base62 encoder
      writes: shortener/encode.py, tests/test_encode.py

These are independent — run them in parallel worktrees.
  (blocked) P1.T3: deps — P1.T1 is open; P1.T2 is open
```

`ddflow claim <ID>` leases the item and binds it to a worktree.

**What a claim holds is what you said it holds.** `claim --globs` is recorded on the item,
so no later heartbeat — live, or reviving a lapsed lease — resets the lease to the globs
the item had before; the claim prints the globs it recorded. Globs are comma-separated or
a JSON array, and the flag may repeat (every value is kept; over MCP a JSON array is read
whole). A glob mangled by quoting is refused with the form every surface reads. `update
--globs` **replaces** the item's globs, and a claimed item's lease with them; the result
names what it dropped, and `show` prints the lease's globs beside the item's. `--resources`
works the same way. `ddflow wait --item X --globs ..` judges the claim you are about to make
rather than the stored globs, so READY means that claim will not be refused.

**Files every item touches** — a changelog, a research log, a regenerated config — would
make every pair of items collide. Declare them, and many leases may hold them at once:

```toml
[lease]
append_only_globs = ["docs/CHANGELOG.md"]   # every item adds lines
shared_globs = ["configs/default.toml"]     # generated: regenerate after merging
```

A path inside either is exempt from the overlap check (`claim`, `update`, `next`,
`wait`) and counts as covered at commit time for anyone holding a live lease. For each
append-only glob in the committed config ddflow **writes** `<glob> merge=union` to
`.gitattributes` — when the knob is set through `ddflow config --set` /
`ddflow_configure`, and again on `ddflow init` / `adopt` after a hand edit — and prints
the line; commit it with the config. A generated file never gets `union` (it would be
interleaved): `doctor` notes a shared glob git has no merge driver for, and reports an
append-only glob git does not union-merge. Git decides which driver a file gets
(`check-attr`), globs match as `.gitattributes` does, and a union line never overrides a
narrower driver the project set for a file inside the glob: it is written before that
line, so the narrower one keeps winning. Only the committed config writes union lines; a
`.ddflow/local` edit syncs nothing.

**When two clones disagree.** A merge can bring in a rival definition of one id (two
clones added it) or two claims on one item whose windows overlapped. ddflow records the
contest instead of letting the later event silently win: `show` prints a `CONTESTED` block
with every rival whole, `next` withholds the item, and `doctor` names it. A person settles
it with one event:

```console
$ ddflow resolve P1.T1 --keep alpha            # a claim: by its holder, or its event id
$ ddflow resolve P1.T1 --keep 3fa9c2 --refile-as P1.T1b   # a definition; the loser is re-added
```

Keeping a claim releases every claim that overlapped it and starts the kept claim's lease
window **now**: a claim that had already lapsed is not stretched back over the gap, so a
claim another agent legitimately made in the meantime is not retroactively contested. A
holder's own lapsed claim, every displaced claim (no cap), and a contestant's late renewal
are all kept in the record. `--keep` may also name the current holder when it met no part
of the contest. An item that is not contested is refused.

**A wrong worktree binding can be corrected.** `ddflow update <id> --worktree PATH` rebinds
the item, and your live lease on it, to that linked worktree and the branch checked out
there. The field edit lands first and the rebind under the same lock, from a fresh read; an
empty path is refused, and `complete`'s tree check then follows the new tree.

**If you are already in one, it adopts that one.** Agent harnesses — Claude Code, Cursor
— often isolate the agent themselves. Claiming from inside a linked worktree binds the
item to *that* tree and branch rather than building a rival and telling you to leave the
one holding your uncommitted work:

```console
$ ddflow claim T1            # run from inside the harness's own worktree
claimed T1 (lease 1800s, renew every 300s)
  worktree: /work/agent-tree  (adopted — you were already in it)
  branch:   agent-work
  Carry on where you are.
```

ddflow never needed to have *created* the tree — it needs to know *which* tree an item
is worked in, so `recover` can find stranded work and `merge` knows what to merge. An
adopted tree is recorded as adopted, not created, so `remove_on_merge` will never delete
something ddflow did not make. A tree already bound to another open item is refused: two
items in one tree cannot be merged or recovered separately — unless that item has let go
of it: merged, lease released, and nothing left in the tree (no uncommitted change, a
`HEAD` its target already contains). `worktree.adopt_existing = false` restores the old
behaviour; `--no-worktree` skips binding entirely. `[worktree] local_files` copies
git-ignored, machine-local files (a `.roborev.toml`) from the primary into every tree a
claim binds — one it creates, the harness tree it adopts, or the item's own tree on a
re-claim — and never overwrites a file already there.

An item claimed `--no-worktree` has no branch of its own, so `merge` and `review` take
one: the branch checked out in the worktree you run them from, or `--branch <branch>`
from anywhere, and `gate run` runs in the worktree you stand in. Two places are not the
item's and are refused rather than guessed: another open item's worktree, and — with
worktrees on — the primary, whose working tree is nobody's in particular (other agents'
event logs, anyone's uncommitted edit); there `merge` refuses, and `review` and `gate
run` record UNAVAILABLE and say what to pass. A lone agent working in the primary says
so with `[worktree].enabled = false`.
The borrowed tree is never removed; uncommitted work in it is refused as for any merge;
and paths the merge lands outside the item's globs are listed (`outside_globs`), since a
borrowed branch can carry another item's commits too.

```console
$ ddflow merge B-fix --branch agent-work
  no worktree of its own: 'agent-work' was landed, no tree touched.
merged B-fix (1c2d3e4f) into main
```

**What `merge` reports and what it leaves alone.** `sha` is the commit the base points at
after the landing — the merge commit, or the branch head on a fast-forward; the forge's
merge commit in PR mode — and `branch_head` is the merged branch's own head, which under a
squash is not on the base at all. A tree you are standing in is kept (its directory is your
shell's cwd; deleting it fails your next command), and `ddflow cleanup --apply` removes it
once the item is complete. A merge that fails is **aborted**, so the primary checkout is
never left mid-merge (`doctor` flags one that was left, with the `git merge --abort` that
clears it). A branch with nothing ahead of its target is refused — it would record the item
merged with nothing landed — unless `--allow-empty`.

A second agent is refused, and told what to take instead:

```console
$ ddflow claim P1.T4 --agent gamma
P1.T4 writes 'shortener/store*.py' which overlaps 'shortener/store.py' held by alpha on P1.T1

You could take instead: P1.T2, P1.T5
```

Exit codes are the contract, and agents branch on them:

| Code | Meaning |
|---|---|
| `0` | healthy |
| `1` | real failure |
| `2` | could not run / nothing to do — **never** collapsed into 0 |
| `3` | coordination refused |

"Nothing is ready" and "everything is fine" are different facts. An agent that cannot
tell them apart invents work.

**The critical path is reported**, because it, not the task count, sets the wall-clock
floor — adding a fifth agent to a phase whose runtime is a four-deep chain buys nothing.
It walks nested sub-tasks: an umbrella's open sub-tasks count as steps before it, and a
phase another depends on contributes its chain. Items held back only by a cap
(`schedule.max_parallel_tasks`, or a resource's capacity) are counted by `status` and `brief`, and the cap's message says when a slot
is free. `ddflow wait` sleeps until something is ready, but when every blocker needs a
person — a dependency cycle, an expired lease under `reclaim_policy = "report"` — it
refuses at once rather than sleeping to its timeout.

---

## Gitflow, pull requests and version tags

Two independent axes in `[flow]`, because teams combine them freely:

| | `integration = "merge"` (default) | `integration = "pr"` |
|---|---|---|
| **`model = "trunk"`** (default) | ddflow as it always was | GitHub flow |
| **`model = "gitflow"`** | gitflow, merged locally | gitflow behind approvals |

Which workflow that makes, and what is not covered:

| Workflow | Supported | Configure |
|---|---|---|
| Trunk-based development | yes | `model = "trunk"`, `integration = "merge"` — short-lived task branches landed straight on trunk |
| Trunk-based with reviews / GitHub flow | yes | `model = "trunk"`, `integration = "pr"` |
| Gitflow (develop, feature/bugfix/hotfix, release branches, tags) | yes | `model = "gitflow"`, either integration |
| Several major trunks, fixes carried between them | yes | `[flow.lines]` + `port_strategy` — see [below](#several-release-lines-fixes-to-older-majors) |
| GitLab flow with environment branches (main → pre-production → production) | yes | `[flow].environments` — see [below](#environment-branches-promoting-downstream) |
| GitLab flow with release branches (upstream first, cherry-picked into stable branches) | yes | `[flow.lines]` + `port_strategy = "cherry-pick"` |

Each of these is a [workflow choice](#workflow-choices-asked-recorded-defaulted-on-the-record):
the operator sets it, or an agent records it, and when nobody does the default is applied
at first use and followed from then on.

**The agent's loop does not change.** `next` → `claim` → work → gates → `merge`. What
`merge` *means* changes with the repository's policy:

* **`merge` in PR mode pushes the branch and opens (or updates) a pull/merge request**,
  then releases the lease and parks the item in **REVIEW**. The agent is free at once and
  takes the next task — nobody waits for a human. It refuses to open a request for work
  whose own pipeline is unfinished: a reviewer's time is the scarce resource, and a
  refusal after the merge could no longer stop anything.
* **`pr sync`** turns what reviewers did back into queue state. `next` runs it for you
  while anything is in review (`sync_on_next`):

  | the forge says | ddflow does |
  |---|---|
  | merged | passes the `merge` gate on the forge's evidence (URL, merge sha), completes the item, retargets anything stacked on it, removes its tree |
  | changes requested | returns the item to the queue **with the review text** (bodies and line comments); `brief` leads with it; a re-claim resumes the same tree and a re-`merge` updates the same request |
  | closed | parks it for a person — a "no" is not something to retry |
  | approved, checks green | merges it (`pr_merge = "on_approval"`, pinned to the approved head) |

* **Stacking keeps work moving through review.** While `T1` waits in review, a task that
  needs it may start **on top of `T1`'s branch** (`stack = true`); its request targets
  `T1`'s branch and is retargeted to the real base when `T1` merges. ddflow never merges
  a stacked request first — that would land it unreviewed inside `T1`'s merge. A task
  depending on two unmerged branches waits: one branch cannot sit on two.
* **Who presses merge** is `pr_merge`: `on_approval` (default — a person's approval is
  still required, and branch protection still applies), `auto` (ask the forge to
  auto-merge when its own rules are met), or `human`.

**Gitflow.** Tasks fork from `develop` as `feature/` or `bugfix/` branches (by tag); a
task tagged `hotfix` forks from production and lands on production **and** develop.
Merges never switch a checkout: a target that is not checked out is merged in a throwaway
worktree, and one checked out in someone else's tree is refused.

**Versions.** `version show` reads the highest `v1.2.3` tag reachable from the release
branch and computes the next version from Conventional Commits (`feat` → minor, `fix` →
patch, `!`/`BREAKING CHANGE` → major; below 1.0.0 a breaking change bumps minor) and from
the tags of items finished since (`breaking`, `feature`, `bug`, `hotfix`). `version cut`
tags it — annotated, with generated release notes. Under gitflow it cuts `release/X` from
develop, merges it into production, tags it and merges the tag back into develop; in PR
mode it opens the release request instead, and `pr sync` tags the merge commit once a
person merges it and opens the back-merge request.

An item counts as shipped on a line when its landing merge commit **or the branch it merged**
(the merge's second parent) is reachable from it: a hotfix landed on production reaches
`develop` by the back-merge as that branch, with production's merge commit nowhere in it. A
squash or fast-forward landing is the one commit it made.

ddflow holds no token: it drives `gh` or `glab`, logged in as the operator, so every
permission question is answered by the forge. A forge that cannot be reached is exit 2 —
"could not ask" is never reported as "nothing changed".

```toml
[flow]
model = "gitflow"          # or "trunk"
integration = "pr"         # or "merge"
pr_merge = "on_approval"   # or "auto" | "human"
pr_reviewers = ["alice"]
```

The research behind this is [RESEARCH R16](docs/RESEARCH.md).

### Several release lines: fixes to older majors

Projects that keep older majors alive — `main` is 3.x while 2.x and 1.x still get fixes —
declare them as **release lines**, oldest first. The newest line is always the *current*
one and follows `model` as above; a maintenance line lands straight on its branch.

```toml
[flow]
current_line = "3"
port_strategy = "cherry-pick"      # or "forward-merge" (the default)

[flow.lines]                        # oldest first
"1" = "maint/1.x"
"2" = "maint/2.x"
```

* **An item belongs to a line**: `task add T --line 2`, or `phase add P --line 2` and
  every task in it inherits it. A line that does not exist is refused, never read as
  "the current one".
* **A fix for several lines** is one command: `task add FIX --lines 1,2,3`. ddflow writes
  it where the strategy says and generates a **port** task `FIX@<line>` for each other
  line — an ordinary task with its own branch, gates and merge or pull request. A port
  starts only once what it carries has *landed* (review is not enough):

  | `port_strategy` | the fix is written on | each port… | ports run |
  |---|---|---|---|
  | `forward-merge` (default) | the **oldest** line | merges the previous line's branch into its own (and passes through every line in between — a merge cannot skip one) | one after another |
  | `cherry-pick` | the **newest** line | applies exactly what the fix landed (the target's before→after range, whatever the merge strategy) with a three-way apply | in parallel |

* **A conflicting port is work, not a failure.** The claim leaves the conflict markers
  in the port's tree and names the files; the agent resolves, commits and carries on.
* **Lines never collide.** The same file on 2.x and on 3.x is two branches, so two agents
  may hold them at once; on the same line the glob check applies as always.
* **Versions per line.** `version show --line 2` reads the highest tag reachable from
  `maint/2.x`; `version cut --line 2` tags it there, and refuses a bump that would leave
  the 2.x major — a breaking change belongs on the current line.

### Environment branches: promoting downstream

GitLab flow's environment branches — each mirroring what is deployed there — are declared
in order, downstream of the current line's target (the base branch; under gitflow,
production, so an environment receives what was *released*):

```toml
[flow]
environments = ["pre-production", "production"]
auto_promote = ["pre-production"]   # optional: continuous delivery to staging
```

* **Work reaches an environment only by promotion, one step at a time.**
  `ddflow promote add pre-production` files a task that merges `main` into
  `pre-production`; `promote add production` merges `pre-production` into `production`.
  So production only ever receives what the environment before it already has
  ("upstream first"). Nothing else targets an environment branch.
* **A promotion is an ordinary task**: claim it and its tree is made from the environment
  branch with the upstream branch already merged in (a conflict is left for the agent,
  like a port's). It runs `gates.promotion_pipeline` — `unit_tests` and `merge` by
  default, since what it carries already passed its own pipeline; add a human gate there
  for a person's sign-off on each deploy. It needs no cross-family reviewer: it authors
  nothing.
* **With `integration = "pr"`** the promotion lands through a merge request into the
  environment branch — the approval *is* the deploy approval — and `pr sync` completes it.
* **One open promotion per environment**, and none when there is nothing to carry (exit 2).
* **`auto_promote`** lists environments `ddflow next` promotes to by itself when the
  branch upstream moves. Empty by default: a deploy is the operator's call.
* `ddflow promote status` shows each environment's head, how many commits it is behind
  the branch upstream of it, and any open promotion.

### Workflow choices: asked, recorded, defaulted on the record

ddflow supports several ways of working and never picks one silently. Each decision —
`model`, `integration`, `pr_merge`, `on_changes_requested`, `stack`, `port_strategy` — is a
**choice**, and its value comes from, in order:

1. **the operator's config** (`.ddflow/config.toml` or env), which always wins;
2. **a recorded choice** — `ddflow flow choose port_strategy cherry-pick --reason "2.x has
   diverged"`, by the operator or by an agent the operator left it to, attributed in the log;
3. **the default** — applied the first time the choice matters (the first claim, the
   first pull request, the first fix filed across lines) **and recorded**, so the project
   keeps following it even if a later ddflow ships a different default.

Until then, a relevant choice nobody made heads `ddflow brief` under *Open workflow
choices*, so an agent asks at the start rather than discovering at the end that the
project wanted something else. `ddflow flow show` lists every choice with its value, its
options, and who decided — config, a named agent or person with their reason, or "DEFAULT
(nobody chose)". A recorded choice that the config file overrides is shown as such, never
silently ignored.

Research: [RESEARCH R17](docs/RESEARCH.md).

---

## Many agents, one server: identity, state and sharing

Several agents and subagents sharing one queue is the case this tool is for. Here is
exactly how that works, because each of these has a wrong answer that looks right.

### Is it stateless?

**The queue is. The connection is not, in exactly one respect.**

The append-only event log is the sole source of truth, and every read re-derives state
from it — `fold(read_all())`, from scratch, on every call. Nothing is cached between
requests, so there is no stale projection, no invalidation, and no divergence between
two agents' views. Restart the server mid-task and nothing is lost: it never had
anything the log did not.

The one piece of per-connection state is **who you are** (below). It is deliberately not
in the log, because it is a property of the caller, not of the work.

### Who is calling?

By default, identity is derived from the working tree. That is right for one agent per
worktree, and **silently wrong for several agents in one tree** — they all resolve the
same path to the same name, their events merge into one stream, `brief` answers with a
sibling's task, and reviewer-independence compares an agent with itself and passes.
Nothing errors. There is no signal that can tell them apart, so identity is **declared**:

| How | When |
|---|---|
| `as_agent` argument (MCP, every tool) | A **subagent sharing its parent's connection** — Claude Code's subagents do. Per call; the connection's identity is untouched. |
| `ddflow_identify` (MCP) | An agent announcing itself on its own connection. Call it first. |
| `DDFLOW_AGENT` env var | A harness that spawns agents and knows their names. Process-wide. |
| `--agent` (CLI) | Scripts and one-off commands. |
| tree-derived default | One agent per worktree. Reported as *undeclared*, so you can see it. `{host}-{tree}-{clone}`: the last part is a random suffix kept in `.ddflow/local/clone-id`, so two clones of one repository never write one shard even on same-named machines. |

Under a declared identity a call uses what *that* agent holds, on every surface: an MCP
tool called with a foreign `as_agent` asks from the primary and `ddflow_claim` creates that
agent's own tree instead of adopting the connection's harness worktree, and `ddflow --agent
X claim` does not adopt a tree another identity is working in. `ddflow_setup` writes into
the tree the server stands in, as `adopt` does; `adopt` run from a linked worktree writes
the project's tracked files into **that worktree** (the event log and hooks stay shared).

A name you set yourself is never suffixed, so the same `DDFLOW_AGENT` in two clones is
still one agent to ddflow; `ddflow doctor` notes a shard whose clock goes backwards, which
is what that leaves behind after a merge.

Innermost wins. `ddflow_identify` is idempotent, persists for the connection, and
refuses a name that could not be a log filename — it becomes one, and refusing at
declaration time means the caller reads the reason rather than discovering it at the
first write.

**If more than one agent works one tree at once, declare identity.** Everything that
attributes work depends on it. A subagent must not call `ddflow_identify` on a shared
connection — that renames its parent — and passes `as_agent` instead; without it, two
subagents claiming the same file are one holder, and a holder's own leases never
conflict with each other.

### Can one server serve several projects?

**No — one server process serves one repository**, fixed at start from `--repo`,
`DDFLOW_REPO`, or the working directory. No tool takes a repo argument, and a test
asserts none ever does. Point a second agent at a second project by running a second
server; they are cheap, and the isolation is the point.

**One project shared by many agents is the supported case** — and the one that needs no
special setup beyond declaring identity:

- **Writes never conflict**, and they never block readers. Each agent appends to its
  **own log shard**, so there is no shared file to overwrite and no merge conflict to
  resolve. Writers *do* serialise briefly: one repo-wide lock is held across the clock
  allocation, the append and its `fsync`. Short, but not nothing — per-agent shards
  remove *file* contention, not lock contention.
- **Reads take no lock at all**, so a read-heavy agent cannot be starved by a write-heavy
  one, and a reader can never block a writer.
- **File ownership is coordinated by `globs`.** `claim` refuses an item whose writes
  overlap one already held, and names what to take instead — exit `3`, not a failure.
- **Lessons, decisions, research and bug history are shared** by construction: they are
  events in the same log, so one agent's finding is immediately visible to every other.

### Locking, contention and measured cost

Measured on this machine, single process, full `read_all()` + `fold()`:

| Events | read + fold | per event |
|---:|---:|---:|
| 500 | 11.5 ms | 22.9 µs |
| 2,000 | 32.6 ms | 16.3 µs |
| 5,000 | 46.0 ms | 9.2 µs |
| 10,000 | 89.2 ms | 8.9 µs |
| 20,000 | 174.7 ms | 8.7 µs |

Linear, converging on **~8.7 µs/event**; the higher figure at small sizes is fixed
per-call overhead, not the fold. A project with 20,000 events pays ~175 ms for a
state-reading call. Search and recall do **not** pay this — they run off a SQLite
projection rebuilt only when the log's head moves.

`tests/test_mcp_load.py` runs 12 concurrent agents through the real MCP surface and
asserts no deadlock, no lost append, no repeated Lamport value within an agent, and
correct attribution for every event — not for a sample. Its thresholds are environment
variables (`DDFLOW_LOAD_AGENTS`, `DDFLOW_WRITE_LATENCY_BUDGET_S`,
`DDFLOW_GROWTH_TOLERANCE`, …) because a load test with a hardcoded budget either flakes
on a shared runner or is too loose to fail.

The deadlock bound is a **hard timeout**: a wedged lock does not fail, it hangs, and an
unbounded hang reads as a broken CI runner rather than as a bug.

---

## Crash recovery

An agent is killed. Nothing is cleaned up, because in a real crash nothing runs.

```console
$ ddflow recover
1 recoverable situation(s); 1 may contain work:

!! P1.T1  [expired_lease]  was: delta
     worktree /repo/../.ddflow-worktrees/P1.T1
     INSPECT FIRST — 1 uncommitted file(s), 1 unmerged commit(s).
     `git -C .../P1.T1 diff main` then salvage,
     then `ddflow release P1.T1 --note salvaged`.
```

Four behaviours, each chosen against a specific way this goes wrong:

- **While the lease is live, nothing happens.** A dead agent is indistinguishable from a
  slow one until the lease expires, and guessing is how two agents end up in one tree.
- **Recovery measures the tree** — uncommitted files, unmerged commits — rather than
  trusting the recorded state. "Is there work in here?" is the only question that decides
  the remedy.
- **An expired lease is never stolen silently**, and `recover --apply` expires only trees
  it measured as *empty*.
- **Adoption, not duplication:** an agent resuming a recovered item gets the *existing*
  worktree back, not a second one beside it.

---

## Reconstruction from logs alone

```console
$ ddflow replay --out ./recovery-kit
wrote:
  recovery-kit/RECONSTRUCTION.md
  recovery-kit/QUEUE.md
  recovery-kit/LESSONS.md
```

`RECONSTRUCTION.md` is written as instructions to a fresh agent, not as a report about
the past: every operator prompt in order, every research verdict, every lesson, the
queue's shape — **and every approach already tried and rejected, with the measurement
that killed it.**

It states its own limit, in the document: it reproduces the *decisions*, not the bytes.
Model outputs are not deterministic, so replaying prompts will not recreate the original
source. What it recreates is every input that produced it, which no other artefact holds.

The demo destroys an entire repository and rebuilds from 3.9 KB of JSONL, then checks
nine specific fragments are present — including the operator's stated *reason* for a
constraint, and the probe output behind a rejected design.

**Secrets are redacted on the way in**, not on the way out — the log is committed, so a
scrub at read time is a scrub that `git show` walks straight past.

---

## Lessons that check themselves

A lesson can name the mistake in **code**, not just in prose:

```console
$ ddflow lesson add --title "Never swallow a bare OSError" \
      --rule "Catch the specific error; a broad except turns a loud failure into a silent one" \
      --pattern "except OSError" --globs "*.py"
lesson La0c745cc recorded — inventory: 2 site(s) now

$ ddflow lesson verify          # later, after somebody adds a third
La0c745cc: 1 NEW site(s): c.py: except OSError:
```

Exit `1` **names the file**. That is the whole design, and it comes from a failure worth
repeating: on the project ddflow was extracted from, a count-based clone ratchet sat red for
~350 commits. It was advisory so it never blocked, it reported a *number* so every reader
learned to skip it, and **24 new clones arrived through that gap**.

> A count says "worse" and never "which".

A number cannot be acted on or reviewed. A list can: a new entry is a line somebody opens,
and a disappeared entry is progress — reported, and never a failure, because the inventory
may only shrink.

Three details that decide whether a ratchet survives contact with a real repository:

- **A site is `<path>: <matched text>`, not `path:line`.** Line numbers churn on every edit
  above a site, which would invent a matching pair of "new site" and "fixed site" findings
  out of an unrelated change — and a ratchet that cries wolf is one that gets switched off.
- **An uncompilable pattern is refused, not stored.** An empty inventory reads exactly like
  a clean repository, and would ratchet every real occurrence away the first time it ran.
- **Exit 2 when no lesson declares a pattern.** Not a pass. A corpus with zero ratchets
  should not be able to report "all clear".

Vendored and untracked files are never sites — matches in code nobody owns are findings
nobody will act on. Most lessons stay prose, and a prose lesson produces no findings at all.

## Checking that the checks are working

Three questions ddflow asks about itself, all derived from the log and all reported by
`ddflow doctor`. They exist because **an unmeasured mechanism is indistinguishable from a
missing one**.

**Can the queue's work actually be picked up?** Every other check counts the items that are
present; this one asks whether any of them can be started. An open phase with no task under
it is work `ddflow next` will never offer — a note by default (`schedule.empty_phase`,
since a project that files phases before breaking them down lives there on purpose) — and a
phase whose tasks are *all finished* while the phase stays open is always a problem, because
that is a queue held open by an item nobody can act on.

Tasks cannot go missing here, and that is a property rather than an untested gap: two
hypotheses about how one could were probed and both refuted, and a test now pins the
invariant so a future filter cannot quietly reintroduce it.

**Does a gate ever say yes?** *A gate that fails on everything is worse than no gate: it
trains the next reader to skip it.* A gate at or above `gates.rate_max_fail` once it has
`gates.rate_min_runs` decisive runs is reported as flaky or as measuring a moving target —
re-running it will not converge. A **skipped** gate is not a run, because counting skips as
failures would make an unconfigured gate look like a broken one.

**Did the periodic passes ever fire?** *The mechanism you did not measure is the one that is
not running.* Because a cadence here counts completions rather than wall-clock, this is exact
rather than estimated: `since` is the completions elapsed since the pass last fired, which is
the **same quantity** `ddflow cadence` uses to decide due-ness — deliberately, because two
measures of "is this behind" that can disagree is a situation nobody can reason about. A pass
more than `cadence.max_missed` scheduled runs behind is reported. Being merely *due* is not a
finding (`ddflow cadence` already says that), and running early is not one either.

All three are **notes, not problems**: a defect in the machinery that checks the work must
not block the work.

## Reading the log, and why it is never compacted

The log only grows, so every state-reading call used to re-read and re-parse all of it.
Measured at 20,000 events, that read costs **115 ms** — and the breakdown is the whole
design argument:

| stage | cost | share |
|---|---|---|
| `Event.from_json` | 97 ms | **84%** |
| `fold` into state | 9 ms | 7% |
| sort by Lamport key | 3.8 ms | 3% |
| read the bytes off disk | 3.7 ms | 3% |
| de-duplicate by content address | 0.7 ms | <1% |

Parsing dominates, and **an append-only file guarantees the bytes already parsed have not
changed.** So `EventLog.read_all` re-parses only the appended tail, and re-hashes the
bytes it is re-using to prove they are still the same bytes:

| events | read, uncached | warm read | |
|---|---|---|---|
| 20,000 | 121.1 ms | **11.9 ms** | 10.2× |
| 100,000 | 623.8 ms | **62.8 ms** | 9.9× |

A command like `ddflow doctor` — which reads four times — pays the full cost once instead
of four times.

Two knobs, `[log]`:

| knob | default | what it trades |
|---|---|---|
| `reuse_parsed` | `true` | Off = always re-parse from scratch. Slower, and worth it only if a shard is being rewritten in place under a running process. |
| `max_cached_events` | `100000` | Memory ceiling, in events. ~736 bytes per parsed event, so the default holds ~74 MB in a long-lived MCP server. Over the ceiling the cache is dropped and reads cost what they always did. |

**The validity check is a content check, and that is the whole design.** The consumed
prefix is re-hashed on every read — 1.1 ms to read plus 4.3 ms to digest, against the
97 ms of parsing it avoids. The first version used `st_ino` instead, on the reasoning
that "a `git merge` writes a temp file and renames, so the inode changes". That is false:

```console
$ git checkout -q other && stat -c %i .ddflow/events/a1.jsonl
218500670
$ git checkout -q main  && stat -c %i .ddflow/events/a1.jsonl
218500670
```

Git rewrites tracked files **in place**. So switching between two branches that had
diverged left a warm server serving events from the branch you left, silently losing the
ones actually on disk, with the tail read starting mid-line — and because `Store.rebuild`
takes its fingerprint from the real file while taking its events from the cache, that
wrong state was written into the SQLite index *stamped as current*, which a fresh process
would not rebuild away. A content digest makes a rewrite, a truncation, a `git checkout`,
a `git merge`, a delete-and-recreate and a torn tail all one case, so there is no list of
mechanisms to keep current.

A **torn final line** from an append that died mid-write is reported by `ddflow doctor`
and re-read until the writer completes it, never marked consumed. Every guarantee here is
mutation-verified in `tests/test_log_read_cache.py` — including that the digest covers the
whole prefix rather than a trailing window of it, which a smaller fixture cannot tell
apart.

### A log from a newer ddflow, and the link event kinds

Checkouts of one repository can run different ddflow versions, and a shard merged from a
newer one may hold event kinds this code has no handler for. They are skipped, as an
unknown config key is, but **not silently**: `ddflow doctor` adds a note naming each
unknown kind and how many events were skipped (`this log has events from a newer ddflow
... skipped: record.extended x2`), and `ddflow status` carries them as `skipped_kinds`.
Every number the older ddflow shows is computed without those events, so the remedy is the
same as for a config key: merge main, or run the newer ddflow.

Two such kinds describe how records relate (decision D-no-duplicates). Add events
(`task.added`, `phase.added`, `bug.found`, `lesson.recorded`, `research.recorded`,
`decision.recorded`, `memory.recorded`) may carry `extends`, `duplicate_of`, `related`
and `dedupe` (the recorded answer, its score and the candidates shown). A later
`record.extended` is a **verbatim addition** to an existing record (text, who, when,
score), and a later `link.recorded` is a link or a `distinct` dismissal. Both accumulate,
keyed by event: two additions made at once by two clones both survive in any fold order,
and an addition never replaces the record's own text (a bug's summary stays as written).
`ddflow replay` renders both. No command writes them yet.

### The compaction that was declined

An event kind `log.compacted` was reserved for a retention pass that shrank the log. It
has been **removed**, because the recipe it was reserved for cannot be implemented
without breaking two shipped commands. Three probes:

1. **A compaction survives a `merge=union` merge.** One branch compacts, the other
   appends; the deletions stick. So union is not the obstacle.
2. **Two divergent compactions merge to neither side's result**, and out of Lamport
   order — the case union cannot resolve.
3. **The decisive one.** `ddflow progress` and `ddflow loops` read **raw events**, not
   folded state: `progress.work` pairs each `lease.acquired` with the next release across
   the whole history. Leases and gate outcomes are not `PROVENANCE_KINDS`, so keeping
   "the last state-bearing event per subject" leaves a `lease.released` with no acquire
   to pair with. A queue whose loop detector fires `repeat_claims` before compaction
   reports **nothing** after it, and six attempts become zero — and under
   `[loops] on_detect = "block"` that is a behaviour change, not just a lost report.

Growth is addressed by making the read cheap rather than the log short, which keeps it
append-only and auditable. Beyond ~100k events the right answer is an on-disk state
snapshot, not a shorter history.

---

## Lessons, research and bugs

```sh
ddflow lesson add --title "Truncating a slug can leave a trailing separator" \
                   --rule "Strip separators AFTER slicing to length, not before."
ddflow lesson search "cutting a url short leaves a dangling hyphen"
```

Retrieval is BM25 over FTS5 and finds that entry despite no shared keyword. Probed
against embeddings and found sufficient at lesson-corpus scale ([R4](docs/RESEARCH.md));
`lessons.search_backend` exists for when that stops being true.

Research entries **must** carry a verdict, and `CONFIRMED`/`REFUTED` are refused without
a probe:

```console
$ ddflow research --question "is it fast?" --verdict CONFIRMED
CONFIRMED requires a --probe (and ideally --probe-output): a verdict with no probe behind
it is an opinion. Use THEORETICAL and say why no probe was possible.
```

And a bug cannot be closed without the test that would catch it again:

```console
$ ddflow bug fixed B1
a bug may not be closed without --regression-test naming the test that would catch it
again. Write the test, watch it FAIL against the unfixed code, then close.
```

That refusal is the whole mechanism by which the same bug does not ship twice.

---

## Cadences

Periodic whole-repo passes a per-task gate structurally cannot do. Due-ness is **derived
from completed work**, so there is no state file to drift:

```console
$ ddflow cadence
DUE: integration_tests — 5 tasks since last (every 5)
DUE: mutation_tests — 3 phases since last (every 3)

Record one with: ddflow cadence --ran <name>
```

Configurable: integration tests, architecture review, mutation testing, duplication
sweep, lessons compression.

---

## Keeping AGENTS.md true

`ddflow adopt` writes a managed block into **AGENTS.md** (and **CLAUDE.md**, and each
agent's native rules file). That block is what tells an agent it must **claim an item
before editing** — and every coordination guarantee here rests on that, because an agent
that does not claim has its work destroyed by a parallel one.

Nothing used to check it again. Adoption is judged by `.ddflow/config.toml` existing, so a
deleted `AGENTS.md`, a block someone stripped, or a block written by an older ddflow all
left the agent reading rules that were absent or wrong while every surface reported the
project as adopted. **Adoption is a config file; the instructions are a separate fact.**

**Cursor does not really follow AGENTS.md**, and it is not alone. Its precedence is Team
Rules > Project Rules > User Rules > `.cursorrules` > `AGENTS.md`, so
`.cursor/rules/ddflow.mdc` is what actually binds — which is why `adopt` writes it. That
file is checked too, for every agent the project was adopted for (read from the driver
deltas on disk, so a Claude-only project is never asked for a Cursor rule).

It carries the **same block** with binding frontmatter, and `alwaysApply: true` is part of
what is verified: a rule with `alwaysApply: false` exists, reads perfectly, and may never be
loaded — which for claim-before-you-edit is the same as not having it, and strictly worse
than drifted text. It is reported at the severity of *missing*, not of *stale*.

Five states are detected — `current`, `stale` (drifted from what this version writes),
`no_block` (file there, block gone), `not_binding` (native rule that will not apply),
`missing` — and reported on three surfaces:

| Surface | What it does |
|---|---|
| `ddflow doctor` | **missing** and **not_binding** are PROBLEMS (exit 1) — the agent has no rules, or has them and will not load them. **stale** is a note, so an upgrade does not turn the health check red. |
| The MCP handshake | A block naming the file, what is wrong, and **ask the operator first**. |
| The footer on tool results | Reports it mid-session, because the handshake fires once. |

**The two surfaces repair it differently, on purpose.**

- **From a shell**, the operator is right there: `ddflow adopt --refresh-docs` rewrites
  the block (plain `ddflow adopt` does too, and also rewrites MCP launches). It
  replaces only what is between the `DDFLOW:BEGIN`/`DDFLOW:END` markers and leaves the rest
  of your file alone, and re-running it is a no-op. `ddflow init` **reports** the problem
  and does not write — writing prose into your `AGENTS.md` is not what `init` was asked to
  do.
- **Over MCP**, ddflow does not touch it. The handshake tells the agent to show the operator
  what is wrong and call `ddflow_setup` only if they agree. It is a file in their
  repository, usually with their own prose around the block, and rewriting it is not a
  decision a tool gets to make on their behalf — the same rule as companions ("propose;
  never install") and the human-approval gate.

---

## Surviving a compaction

The instruction block reaches the model **once**, at connect. After a context compaction it
may retain none of it, and MCP has no server-to-client primitive for injecting context —
the three that exist (`roots/list`, `sampling/createMessage`, `elicitation/create`) all go
the other way or ask a question. Three things already survive:

- the **AGENTS.md / CLAUDE.md sections** `ddflow adopt` (or `ddflow_setup`) writes, plus each agent's native
  rules file — the client re-reads its own rules, so this is the durable channel;
- the **commit hook**, which refuses a commit with no item trailer and says what to add.
  Enforcement at the moment of the act needs no context at all;
- `ddflow help <topic>`, which the agent can ask for — if it thinks to.

What none of those do is **speak up unprompted**. A footer on tool results is the only
channel that is guaranteed to be heard again, because an agent driving ddflow calls tools
continuously:

```
ddflow: left undone in this project —
  · 1 bug(s) still open: B1 — close with `ddflow_bug_fixed` (it requires the regression test) or say why not
  · 2 gate(s) skipped, not run: T4.critic, T4.standards — run them, or leave the skip on the record deliberately
```

**It is not a banner, and the difference is the whole design.** A fixed reminder appended to
63 tools is trained out inside a session and costs tokens on every call. This one:

- **names what happened**, never restates a rule — an id, a count, and the call that
  discharges it;
- **stops** once the thing is dealt with, so it cannot be trained out by repetition;
- **says nothing at all** when the project has nothing outstanding — not a cheerful "all
  clear", which is the same thing readers learn to skip;
- is **cadenced**: at most once every `every_calls` calls *and* `every_seconds` seconds, so
  a burst of calls is not a burst of footers;
- **cannot break the call it rides on**. It is a courtesy on top of an answer, appended
  after the body, and a failure inside it is swallowed. `content[0]` is still the
  structured result.

```toml
[reinstruct]
enabled      = true   # false silences it entirely
every_calls  = 12
every_seconds = 240
max_items    = 3
```

What it currently notices: bugs found and never closed, gates skipped and never revisited,
and work finishing with no lesson ever recorded (after `[lessons] reflect_after_items`, so
one task is not reported — the pattern is, and the threshold is a knob because where the
line sits is a judgement).

---

## Keeping session-start cost flat

```sh
ddflow brief --phase P2
```

Returns, inside `session.brief_max_tokens` (default 1200): recoverable work first, then
the current item and its remaining gates, then what is ready, then why everything else is
blocked, then the handful of past lessons **ranked against this task's text**.

This *replaces* reading the project's rule and lesson corpora. The budget is enforced by
truncating from the bottom, so the safety-critical head survives a squeeze — and a
project's opening cost stays roughly constant as its lesson corpus grows.

---

## Agent portability

One canonical driver, [`templates/drivers/implement-phase.md`](ddflow/templates/drivers/implement-phase.md),
plus a **delta** per agent covering only what genuinely differs: how iteration continues,
how to ask the operator, how to spawn a subagent, file-reference syntax.

Deltas rather than copies, for a measured reason: on the project this was extracted from,
a reworded per-agent duplicate of the driver silently accumulated three instructions that
were false at the time of writing while missing four gates the canonical file had gained.
A delta removes the surface that can drift instead of policing it.

Both surfaces are one implementation — the MCP server maps each tool onto the same
`cli.main()` call in-process, and two tests plus a demo step assert they cannot diverge.

| Agent | Reads | MCP config written by `adopt` |
|---|---|---|
| Claude Code | `CLAUDE.md` → driver | `.mcp.json` |
| Gemini CLI | `AGENTS.md` | `.gemini/settings.json` |
| Codex CLI | `AGENTS.md` | `.codex/config.toml` |
| GitHub Copilot | `.github/copilot-instructions.md`, `AGENTS.md` | `.vscode/mcp.json` |
| Kilo / Cline | `AGENTS.md` | `.kilo/kilo.json` |
| CI / Make / human | — | none; the CLI is complete on its own |

---

## Keeping the two surfaces honest

Every CLI command is reachable over MCP — that is the point of the tool list, and it is
the requirement that an operator in a chat window, possibly driving a remote agent, can
do everything a shell can. Three ratchets keep it true, and each one was added after the
previous one turned out to be too shallow:

| Ratchet | What it caught on its first run |
|---|---|
| every CLI **command** has a tool | the original check |
| every CLI **subcommand** has a tool | `ddflow gate skip` and `bug found` had none — `gate` counted as "covered" by `gate run`, and a parent's coverage says nothing about its children |
| every CLI **flag** is reachable from its tool | **27 divergences** — 16 on its first run, and 11 more the moment it derived its own coverage instead of using a hand-written list. Including `phase add --globs`: over MCP a phase could not declare what it writes, so the conflict detector had nothing to compare at phase level |

The flag ratchet derives its own input from the parser rather than a hand-written list —
its first version carried eleven tools and was blind to `remove --force` for exactly
that reason. Omissions are allowed, but each must be an entry in `FLAG_EXEMPTIONS` with
its reason, so "we chose not to expose this" and "nobody noticed" stop looking alike.

A fourth pins something subtler: **whether a tool returns JSON or prose is a decision,
not an accident.** Some tools deliberately return prose — `brief`, `gate status` and
`replay` exist to hand the model an *instruction* or a narrative, and JSON-encoding a
paragraph so the client can decode it again helps nobody. But `decision add` returned
JSON while `task add` returned prose for no reason either could state. Each prose tool
now carries its justification in `PROSE_TOOLS`.

---

## Command reference

```
ddflow adopt [--agents ...]     install into a project, for one or more agents
ddflow adopt --refresh-docs     rewrite only the driver docs + rules blocks (doctor says when)
ddflow init                     create .ddflow/ only

ddflow phase add <id> [...]     add a phase
ddflow task add <id> --phase .. add a task
ddflow update <id> [...]        change title/body/needs/globs/tags/priority (--globs REPLACES)
ddflow update <id> --worktree P rebind the item and its lease to the linked worktree at P
ddflow resolve <id> --keep X    settle a contested item: keep one definition or one claim

ddflow next [--phase P]         what may start now       (2 = nothing actionable)
ddflow claim <id> [--globs ..]  lease + create worktree  (3 = refused)
ddflow heartbeat <id>           renew a lease
ddflow release <id>             give it up

ddflow gate status <id>         pipeline position + the next gate's instruction
ddflow gate run <id> <gate>     execute a command gate, record its evidence
ddflow gate record <id> <gate>  record an agent gate    (--outcome, --reason, --model)
ddflow gate skip <id> <gate>    skip, with a mandatory reason
ddflow approve <id> <gate>      a PERSON clears a human gate  (no MCP equivalent)
ddflow approve .. --reject      ...or refuses it, with --reason
ddflow gate verify <id> <gate>  prove the gate CAN fail  (1 = it cannot)
ddflow tests [--item <id>]      tests the change reaches + a parallel command  (2 = none)

ddflow merge <id>               merge from the primary checkout, no checkout
ddflow merge <id> --allow-empty land a branch with nothing ahead of its target (refused otherwise)
ddflow merge <id> --branch <b>  an item claimed --no-worktree: land <b> (default: your tree's branch)
                                ([flow].integration=pr: push + open/update a PR instead)
ddflow pr sync [--item]         what reviewers did: complete / reopen / park / merge (2 = forge unreachable)
ddflow pr status               every item's request, from the log (no forge call)
ddflow version show            current and next version, why, release notes (2 = nothing new)
ddflow version cut [--push]    tag it (gitflow: via release/X, or a release PR)
ddflow version show|cut --line L    the same, for a maintenance line (keeps its major)
ddflow task add <id> --lines 1,2,3  a fix for several release lines: ports generated
ddflow promote add <env>        file a promotion one step downstream (2 = nothing to carry)
ddflow promote status           each environment: head, behind upstream, open promotion
ddflow flow show                how this project works: model, lines, every choice + who made it
ddflow flow choose <knob> <v>   record a workflow choice, with --reason
ddflow complete <id>            finish        (3 = unmet conditions, all listed)
ddflow block <id> --reason ..   mark blocked
ddflow abandon <id> --reason .. stop work on an item without completing it
ddflow remove <id> [--force]    take an item out of the queue (recorded, not erased)

ddflow brief [--item|--phase]   budgeted session-start pack
ddflow board / show <id>        human views (show also takes a bug id; --json board is JSON)
ddflow wait [--item|--phase]    sleep until something is ready  (--globs: judge the claim you will make)
ddflow render                   regenerate docs/ddflow/*.md

ddflow help [topic]             what this is, what it can do, the workflow
ddflow workflow                 the rules this project runs by  (1 = incoherent)
ddflow workflow pipeline ...    set the gates a task or phase passes
ddflow workflow gate ...        define or change one gate
ddflow workflow drop <id>       take a gate out of the pipelines
ddflow import [--apply]         propose an existing project's work  (2 = nothing)
ddflow import --verify          is the import still true, and did anyone finish it?
ddflow history [--item|--kind]  one timeline of everything that happened (2 = nothing)

ddflow lesson add|search        capture and retrieve lessons
ddflow research --verdict ..    record a finding (probe required for CONFIRMED/REFUTED)
ddflow bug found|fixed          regression test required to close (--regression-test repeats)

ddflow session start|prompt|note|end     provenance logging
ddflow replay [--out DIR] [--verify]     reconstruct from the log

ddflow recover [--apply]        find crashed agents' work   (2 = nothing)
ddflow cleanup [--apply]        classify ddflow worktrees/branches; --apply lands the safe ones
ddflow pins <file>              which text of an instruction file a test pins, before you compress it
ddflow precommit [--write]      propose a .pre-commit-config.yaml for this repo's stacks (writes only with --write)
ddflow doctor                   integrity + health
ddflow rebuild                  re-derive the index
ddflow cadence [--ran NAME]     which periodic passes are due  (2 = none)
ddflow config --explain         every knob, its value, its source and its docs
ddflow config --append-toml ..  add config without a shell editor (validated first; --local: not committed)
ddflow reviewers detect|list|test   find and check cross-family review endpoints
ddflow review <id> --gate ..    run the configured reviewer, record the evidence
ddflow review <id> --gate G --chunk N   re-review only chunk N of the recorded review
ddflow review triage <id> --gate G --finding N --refuted|--confirmed --probe ..
                                record what became of one finding
ddflow reviewers approve [name] a PERSON approves a tool-written reviewer  (no MCP tool)
ddflow mcp                      run the MCP stdio server
```

Every one of these is reachable over MCP, and a test enforces it. One tool goes the
other way and has **no CLI equivalent**, because it has nothing to mean there:

```
ddflow_identify(agent=...)      declare who you are ON THIS CONNECTION (MCP only)
```

A CLI invocation is one process that exits, so it says who it is with `--agent` and the
question does not outlive the command. An MCP connection is a session, so identity is
declared once and persists — see
[Who is calling?](#who-is-calling).

---

## Configuration

139 knobs across 18 sections, every one documented in place:

```console
$ ddflow config --explain --filter lease
lease.ttl_s = 1800   [default]
    Seconds a lease stays valid without a heartbeat. After this it is EXPIRED and
    reclaimable. Longer = fewer false expiries when an agent is deep in a slow gate;
    shorter = faster recovery after a crash.
```

Resolution: dataclass defaults → `.ddflow/config.toml` → `DDFLOW_<SECTION>_<KNOB>` env.
An unknown knob **in a file** is never a silent drop: ddflow skips it, so a config written
by a newer ddflow does not stop an older checkout, but it warns on every command and
`ddflow doctor` reports it as a problem (a typo, or a config newer than this code — merge
main). `ddflow config --set` refuses an unknown knob or an invalid value outright, before
writing. A map or list knob (`list[str]`, `dict[str, str]`) given by environment as JSON
refuses a non-string element instead of casting it (`null` is not the string `"None"`).
A test asserts every knob carries documentation, so the reference cannot rot.

---

## What is automated, and what is not

The honest split, because a tool that claims to automate judgement is lying about the
part that matters.

**Automated — happens without anyone remembering it:**

* **The handshake briefs the agent.** On connect, the MCP server injects the live state:
  the pipeline every task must pass, work recoverable after a crash, what is ready, which
  companions are missing, and what to do about each. It is a template
  (`ddflow prompts eject mcp_instructions`), so the workflow is text you edit, not code
  you fork.
* **Gates are enforced, not suggested.** `ddflow complete` refuses on a required gate
  that has not passed, on open sub-tasks, on a silent gate under `require_outcome`, on a
  requirement no pipeline runs, and on a reviewer from the author's own family. Refusals
  list *every* unmet condition, not the first — an agent that cannot see how many more
  are coming reaches for `--force`.
* **Conflicts are refused at claim time**, by glob overlap, with an alternative named.
* **Dependencies gate readiness.** `ddflow next` withholds a task whose `needs` are open
  and says which.
* **Bugs are offered before features.** A task tagged `bug`/`fix`/`hotfix` (the
  `[flow]` bugfix and hotfix tags), or named by an open bug record, comes ahead of every
  feature in `ddflow next` and gets a free slot first, so a standing bug is fixed before
  more work is built on it. Priority orders each group; `[schedule] bugs_first = false`
  orders by priority alone.
* **Gate evidence records which tree and how much** — a working-tree fingerprint plus
  files/lines changed — so a pass names what it passed on. If the tree moves afterwards,
  `complete` warns that the evidence describes source nobody is shipping.
* **Crash recovery**: `ddflow recover` finds worktrees whose lease expired, so an
  interrupted agent's work is found rather than lost.
* **Cadences** (`ddflow cadence`) tell you which periodic passes are due — bug hunts,
  dedupe, lesson compression — from the log, counted in completed work; and, for a rule
  like "a bug hunt every week", by the calendar (`[cadence] every_days =
  ["bug_hunt=7"]`: never run means due now). The SessionStart hook lists what is due.
* **Post-merge review**: `ddflow review <item> --commit <sha>` reviews one landed
  commit against its first parent — a merge as what it brought in — when the branch
  is gone.
* **A commit hook** (`ddflow hooks install`) can refuse an unclaimed edit outright,
  and refuses a staged `ddflow render` view that the log no longer regenerates
  byte-for-byte — hand-edited, or stale (`[enforce] generated_views`). It also reports
  a doc line still naming an identifier, file or default the commit removes or renames
  (`[enforce] stale_docs`, `doc_globs`, `doc_exclude`; warns by default). It refuses a
  commit on a branch whose base changed a rulebook (AGENTS.md, CLAUDE.md, each agent's
  native rules file, the driver docs) since it forked (`[enforce] stale_rules`), and warns
  past `max_behind` commits behind (`[enforce] behind`); the commit concluding
  `git merge <base>` is exempt, and the session hook only informs. Its commit-msg
  sibling requires an `Item:` trailer when `[enforce] require_item_trailer` is on — or
  the project's own keys (`item_trailer_keys = ["Phase", "Phase-ships"]`); merges are
  exempt. The value must be the id of an item in the queue (any state but removed): a
  mistyped id is refused with the nearest real ones, and a queue the hook cannot read is
  exit 2, never a pass. A key that marks a commit shipping no item takes only its declared
  words instead (`trailer_waivers = { "Phase-ships" = ["none", "recon"] }`). It also refuses any trailer named in `[enforce] forbidden_trailers` (e.g. a
  tool-attribution line), merges included, for every agent and every route that runs
  git hooks -- which a harness-side hook reading only the command text cannot promise.
  The hook's refusals say what to do. A **lapsed lease** on the item whose tree you commit
  from is named with when it lapsed and its remedy (`ddflow --agent <holder> heartbeat <id>`
  for its holder; for anyone else `recover --item`, `release --note salvaged`, `claim`). When
  nothing declared who is committing, it says the name was derived from the tree and how to
  declare one. A **clean merge commit** — the automatic one concluding `git merge <base>`,
  or a squash that names what it squashes — passes without a lease lookup, and a conflicted
  merge is judged only on the paths the merge changed, not on everything the base brought
  in. `ddflow merge` itself aborts a merge it fails, and `doctor` flags a primary checkout
  left mid-merge with the command that clears it.
  Over a hook the **pre-commit framework** generated, `hooks install` does not edit it (the
  next `pre-commit install` would discard the edit): it advises a `repo: local` hook in
  `.pre-commit-config.yaml` (`ddflow precommit` proposes it), says nothing needs installing
  when the framework already runs ddflow's check, and `--force` replaces the generated hook.
* **A per-task README check** (decision D-readme-current). A task whose diff changed
  code under `[enforce] readme_code_globs` (default `ddflow/**`) but none of
  `readme_files` (default `README.md`), with no `docs` outcome recorded for it, is
  reported — `README not updated: record the section you changed, or gate skip docs
  --reason ...` — by `ddflow complete`, in `ddflow gate status`, and as a `docs:` line
  under the item in `ddflow brief`. Record the reason with
  `ddflow gate skip <id> docs --reason "..."` (or `gate record <id> docs --outcome
  passed --evidence "<section>"`). `[enforce] readme_with_code` picks the severity:
  `warn` (default), `block` (refuses `complete`) or `off`. Test files (`tests/`,
  `test_*`, `*_test.*`, `*.test.*`, `*_spec.*`), documentation (`docs/`,
  `.md`/`.rst`/`.adoc`/`.txt`) and ddflow's own event-log commits (`.ddflow/**`) are never
  reported; when git cannot say what the task changed, `complete` says the check could not
  run (a warning, never a block). It is a check, not a pipeline gate, so a task with
  nothing to document is not asked for a `docs` outcome; the phase-level `docs` gate is
  unchanged.
* **A Claude Code SessionStart hook** (`ddflow hooks install --claude`) puts the brief —
  crashed work to recover, ready items, binding decisions, operational memory — into
  every session, including after a context compaction, whether or not the agent
  remembers to ask. In a worktree behind its base branch it says so, and names the
  rulebooks that changed there. It is added beside the project's own hooks in
  `.claude/settings.json`, removed alone, and always exits 0.

**Not automated, on purpose:**

* **Installing anything.** Detection is read-only; the report is advice.
* **The judgement inside an agent gate.** ddflow records that you claim to have hunted
  bugs; it cannot check that you did. What it *can* do — and does — is make silence
  visible: a gate never run and never skipped blocks completion, so the failure mode is
  a refusal rather than a quiet omission.
* **Deciding whether a plan is right.** That is what a `human = true` gate is for.
* **Pushing, releasing, or anything outward-facing.**

The design assumption is that an agent's *honesty* cannot be verified, so the system is
built to make an unverifiable claim expensive to make and easy to see: evidence
contracts, mutation-verified gates, coverage gaps recorded on completion, and an exit
code that distinguishes "could not" from "did not need to".

---

## Testing

```sh
uv run pytest tests/ -q -n auto      # unit/integration tests, in parallel (pytest-xdist)
python3 demos/run_all.py             # 6 end-to-end scenarios, 219 assertions
```

While working on an item, `ddflow tests --item <id>` (MCP: `ddflow_tests`) lists the
tests the change reaches — changed test files, tests importing a changed module directly
or one step removed, tests named after a changed file, everything under a changed
`conftest.py` — each with why, and prints one command that runs them in parallel with
the project's own runner and worker flags. It is fast feedback and never a gate: the
`unit_tests` gate always runs the whole suite, in parallel, because a targeted run hides
breakage that was already there. The agent driver tells agents to run it after each
change; `ddflow workflow` and `ddflow doctor` say when the configured test command uses
one core.

The demos invent whole projects and drive them for real — real git worktrees, real
`pytest` and `npm test` runs, real merges, real concurrent processes:

| Scenario | What it proves |
|---|---|
| `parallel-phase` | Two agents build a URL shortener in parallel; a third is refused on a file conflict; a dependent task unblocks automatically when its last dependency lands |
| `crash-recovery` | An agent is killed holding uncommitted work; it is found, measured, never stolen, and adopted intact on resume |
| `reconstruct-from-log` | The entire repository is deleted; everything rebuilds from 3.9 KB of JSONL, and nine specific facts are checked present |
| `mcp-polyglot` | A Node.js project driven end-to-end over real MCP JSON-RPC, with both surfaces asserted to agree |
| `mcp-orchestration` | **A whole two-phase Python library built by two agents entirely over MCP** — bootstrap, configure, discover a reviewer, fan out, get refused by the hook, real pytest, a real cross-family review, merge, close both phases, reconstruct. 24 steps, 57 assertions. |
| `full-lifecycle` | **26 steps, 89 assertions — the whole arc, from an operator's first sentence to a rebuild from the log.** A double-entry bookkeeping library across two dependent phases with sub-tasks: the operator states requirements in English, a decision is recorded and scoped to the files it governs, two agents fan out, a task turns out to be two concerns and grows sub-tasks, a bug is found and may not be closed without its regression test, a phase closes on its own pipeline, a new requirement arrives **while a task is in flight**, that task is **split in place**, the guardrails are tested by trying to break them, and finally every `.py` file is deleted and the project is reconstructed from the log alone. |

**The scenarios and the stress test have found most of the bugs this project fixed; the unit tests found few of them.** The `full-lifecycle` scenario was written to exercise the *requirements* rather than the code, and found four defects before it passed once — every one of them a CLI/MCP divergence that command-level parity could not see. The composed MCP run alone found eight that 213 unit tests and four other scenarios missed — including two that made core features useless out of the box. They all lived in *seams*: between two processes, between a read and a write, between two output surfaces, between a declared vocabulary and its callers, including one that does
not reproduce below ~6 concurrent processes. They are catalogued with their regression
tests in [R6](docs/RESEARCH.md#r6--bugs-this-project-found-in-itself).

---

## Documentation index

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The event-log inversion, ordering, concurrency, module map, what is deliberately absent |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Twelve research questions with probes, measured output and verdicts; the self-found bug catalogue, the 2026-09-24 review pass (R10), the importer against a real 400-day corpus (R11), and what the MCP spec is worth for a mutating tool (R12) |
| [docs/RECOVERY.md](docs/RECOVERY.md) | Operator runbook: crashes, corruption, divergence, full reconstruction |
| [templates/drivers/implement-phase.md](ddflow/templates/drivers/implement-phase.md) | The canonical agent-agnostic driver |
| [templates/drivers/deltas/](ddflow/templates/drivers/deltas/) | Per-agent deltas: Claude, Gemini, Codex, Copilot, Kilo, Cursor |
| [probes/](probes/) | Runnable probes behind the research verdicts |
