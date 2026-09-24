{#
  What the MCP client injects into the model's context the moment it connects.

  This is the ONLY place the server speaks unprompted, and it is plain text on purpose:
  it is the file to edit when you want a different workflow, a different tone, or a
  different set of companion tools. Nothing here is compiled in.

    orchard prompts eject mcp_instructions      -> .orchard/prompts/mcp_instructions.md
    [prompts] mcp_instructions = "path/to.md"   -> anywhere else

  Variables available (see `mcp_server._instruction_vars`):
    adopted            bool    .orchard/config.toml exists
    setup_todo         list    named setup gaps, each a sentence
    companions         list    {id, title, gates, state, install, url, default}
    missing_companions list    the default ones not registered here
    gate_gaps          list    gate ids in the task pipeline with no companion behind them
    recoverable        int     crashed agents' worktrees waiting
    ready, running, blocked, open_bugs, loops   int
    task_pipeline      list    the gate ids every task passes through, in order
    require_outcome    bool    whether a silent gate blocks completion
#}
{% if adopted %}
This repository's work is a queue managed by Orchard. Follow it — the rules below are
enforced by the tools, not merely requested.

**Call `orchard_brief` FIRST, before anything else.** It returns work left over from a
crashed agent, what is ready to start now, why everything else is blocked, the
architectural decisions that govern the files you are about to touch, and the past
lessons ranked against this task. It replaces reading this project's rule and lesson
files — do not read those instead; they are long and it has already ranked them.

**Claim before you edit.** `orchard_claim` leases an item and gives you an isolated git
worktree. An unclaimed edit can be destroyed by a parallel agent, and in this repository
the commit hook may refuse it outright.

**The loop:** `orchard_next` → `orchard_claim` → work in the worktree →
`orchard_gate_status` and satisfy each gate → `orchard_merge` → `orchard_complete`.

**The pipeline every task passes through, in order:**
{% for g in task_pipeline %} {{ g }} ·{% endfor %}

{% if require_outcome %}
Every one of those must carry an outcome before `orchard_complete` will finish the item.
Silence is not a pass. If a step genuinely does not apply, say so on the record with
`orchard_gate_skip <id> <gate> --reason "..."` — that names the one step you dropped,
where `force` would override all of them at once.
{% endif %}

## Reporting — what you record, and when

Record as you go, not at the end. A session that reports nothing is indistinguishable
from a session that did nothing, and the log is what lets this project be rebuilt.

- **The operator's words, verbatim** — `orchard_session_prompt` for each instruction you
  are given. Not a summary. `orchard_replay` reconstructs the project from these, and a
  paraphrase written afterwards reconstructs the paraphrase.
- **A decision, the moment it is settled** — `orchard_decision_add`, with `globs` set to
  the code it governs so the next agent to touch those files is told, and
  `alternatives` set to what you rejected and why. Without that, the next agent
  re-proposes it.
- **A bug, when you find it, BEFORE you fix it** — `orchard_bug_found`.
  `orchard_bug_fixed` then refuses to close it without naming the regression test, so a
  bug that was never opened is a fix that never had to prove itself.
- **A lesson, after any surprise or any correction from the operator** —
  `orchard_lesson_add`. Write the transferable rule, not the incident.
- **Research, with its verdict** — `orchard_research_add`, `CONFIRMED` / `REFUTED` /
  `THEORETICAL`. A verdict with no probe behind it is an opinion; say so by labelling it
  `THEORETICAL`.
- **Gate outcomes with their evidence** — the command you ran and its exit code. A gate
  in `gates.evidence_required` rejects a bare "it passed".

A tool or reviewer that could not run is recorded `unavailable`, **never** `passed`.
Exit code 2 means "could not run / nothing to do" — it is a result, not an error, and
never a success.

## Tools this workflow expects you to use

Orchard imposes the order and demands the evidence. It does not perform the judgement
inside most gates — these do. Use them where they apply; when one is absent, record the
gate `unavailable` rather than passing it on your own word.

- **roborev** — `standards`, `bug_hunt`, `dedupe`. Review the phase's commit, and run
  its duplication analysis across the files you touched. That one earns its place: it
  sees cross-file drift a per-file review cannot, which is the characteristic failure of
  agent-written code — an agent changing replicated logic updates one copy and misses
  the rest. Apply its FINDINGS; verify its FIXES by running the tests.
- **codeguide-mcp** — `standards`. Fetch the guide for the languages in play and check
  against a written standard rather than your own taste. Read its agentic-workflow guide
  before any non-trivial task.
- **context7** — `research`, `standards`. Resolve the library, then fetch current docs
  for any API you are about to use. Your memory of a library's API is exactly the kind
  of claim that is cheap to check and often wrong.
- **OptMem** (`scripts/memo`) — `rules`. Cross-session memory of operational facts about
  *this machine and working state*. Orchard's own `orchard_recall` covers the project's
  memory — decisions, lessons, research, bugs, past prompts — and is the one to reach
  for first; OptMem covers what is true of the environment, which is a different thing.

{% if missing_companions %}
**Not wired up here:** {% for c in missing_companions %}`{{ c.id }}` {% endfor %}
Call `orchard_companions` for the state of each and the command that installs it. Say so
to the operator rather than quietly doing without: a gate with nothing behind it passes
on one model's unaided assertion, which is the failure this pipeline exists to prevent.
{% endif %}
{% if gate_gaps %}
Gates in this project's pipeline with no tool behind them:{% for g in gate_gaps %} `{{ g }}`{% endfor %}.
Several of those are judgement you do directly — that is fine and expected. It is the
list to re-read when a gate has been passing suspiciously easily.
{% endif %}

## Before you start anything non-trivial

`orchard_recall` — one search across every decision, lesson, research verdict, past bug,
similar task and earlier operator prompt. It exists so the operator does not have to say
the same thing twice and you do not have to learn the same lesson twice.

{% if recoverable %}
## Waiting for you right now

{{ recoverable }} worktree(s) from a crashed agent hold work that exists nowhere else.
Call `orchard_recover` and deal with them BEFORE picking up anything new. Nothing is
ever stolen or deleted automatically, which is why it is still there.
{% endif %}
{% if loops %}
## The queue is looping

{{ loops }} loop finding(s) are open — repeated claims, a flapping gate, work that will
not stay done, or a dependency ring. `orchard_loops` names each with its evidence and
its remedy. A ring is broken by removing one edge: `orchard_update <id> --needs ""`.
{% endif %}
{% if setup_todo %}
## Setup still needed

{% for t in setup_todo %}- {{ t }}
{% endfor %}
{% endif %}
{% else %}
This repository does not use Orchard yet.

If the user wants a managed work queue — phases and tasks with dependencies, parallel
agents in isolated git worktrees, a quality pipeline that refuses to pass a step nobody
ran, and crash recovery — call `orchard_setup` ONCE. It creates `.orchard/`, writes the
driver, adds a short section to `AGENTS.md` describing how work is claimed here, and
reports which companion tools (roborev, codeguide-mcp, context7, a memory server) are
present on this machine and which are missing.

Then set the project's test command with `orchard_configure`, wire up what
`orchard_companions` reports missing, and add work with `orchard_phase_add` /
`orchard_task_add`.

Do not call the other tools before `orchard_setup`; they will report that there is no
queue.

If the user has not asked for any of this, say nothing about it and carry on.
{% endif %}
