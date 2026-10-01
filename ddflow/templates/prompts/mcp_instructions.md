{#
  What the MCP client injects into the model's context the moment it connects.

  This is the ONLY place the server speaks unprompted, and it is plain text on purpose:
  it is the file to edit when you want a different workflow, a different tone, or a
  different set of companion tools. Nothing here is compiled in.

    ddflow prompts eject mcp_instructions      -> .ddflow/prompts/mcp_instructions.md
    [prompts] mcp_instructions = "path/to.md"   -> anywhere else

  Variables available (see `mcp_server._instruction_vars`):
    adopted            bool    .ddflow/config.toml exists
    setup_todo         list    named setup gaps, each a sentence
    companions         list    {id, title, gates, state, install, url, default}
    actionable_companions list  every default one not already usable, each carrying a
                                `state_word`: "installed, not registered" / "not
                                installed" / "not checked". ONE list, because the
                                proposal an agent makes is the same in all three cases
                                and only the claim about install state differs.
    missing_companions     list  the subset KNOWN to need action (not "not checked")
    unregistered_companions list  installed here, no agent configured to launch it
    uninstalled_companions  list  known absent; `install` is the command
    unchecked_companions    list  nobody probed (the handshake does not) — NOT "absent"
    rules_drift        list    {path, state, detail} for each rules file that is missing,
                               stripped or drifted. Empty when they are current.
    gate_gaps          list    gate ids in the task pipeline with no companion behind them
    recoverable        int     crashed agents' worktrees waiting
    ready, running, blocked, open_bugs, loops   int
    task_pipeline      list    the gate ids every task passes through, in order
    require_outcome    bool    whether a silent gate blocks completion
    importable         int     source files an import could read
    queue_is_empty     bool    nothing in the queue at all
    imported_total     int     items/records carrying import provenance
    imported_no_globs  int     imported tasks with no declared globs
    imported_shipped_drift int phases claiming SHIPPED over an open task
#}
{% if adopted %}
This repository's work is a queue managed by ddflow. Follow it — the rules below are
enforced by the tools, not merely requested.

**Call `ddflow_brief` FIRST, before anything else.** It returns work left over from a
crashed agent, what is ready to start now, why everything else is blocked, the
architectural decisions that govern the files you are about to touch, and the past
lessons ranked against this task. It replaces reading this project's rule and lesson
files — do not read those instead; they are long and it has already ranked them.

**Claim before you edit.** `ddflow_claim` leases an item and gives you an isolated git
worktree. An unclaimed edit can be destroyed by a parallel agent, and in this repository
the commit hook may refuse it outright.

**If you are one of SEVERAL agents or subagents on this repository, call
`ddflow_identify` first.** Identity is what attributes every claim, gate outcome and
review. Unasked, it is derived from the working tree — correct for one agent per tree,
and wrong with no error for several in one tree: your work and theirs merge into a
single identity, `ddflow_brief` answers with someone else's task, and a review gate
compares you against yourself and passes. Nothing can detect this from the outside, so
say who you are: a short stable name for your role or assignment. Working alone in your
own worktree, skip it.

**The loop:** `ddflow_next` → `ddflow_claim` → work in the worktree →
`ddflow_gate_status` and satisfy each gate → `ddflow_merge` → `ddflow_complete`.

Where merges go through pull requests (`[flow].integration = "pr"`), `ddflow_merge` opens
the request and parks the item IN REVIEW with your lease released: do not wait and do not
`ddflow_complete` it — take the next item. `ddflow_next` syncs reviews itself: merged work
completes, and an item with requested changes returns to the queue with the review at the
top of its `ddflow_brief`.

How THIS project works — branching model, release lines, how fixes reach older lines — is
`ddflow_flow_show`. A choice nobody has made is listed in `ddflow_brief`: ask the operator,
or choose what suits the project with `ddflow_flow_choose` and a reason. Left alone, the
default is applied the first time it matters and followed from then on.

**The pipeline every task passes through, in order:**
{% for g in task_pipeline %} {{ g }} ·{% endfor %}

{% if require_outcome %}
Every one of those must carry an outcome before `ddflow_complete` will finish the item.
Silence is not a pass. If a step genuinely does not apply, say so on the record with
`ddflow_gate_skip <id> <gate> --reason "..."` — that names the one step you dropped,
where `force` would override all of them at once.
{% endif %}

## Reporting — what you record, and when

Record as you go, not at the end. A session that reports nothing is indistinguishable
from a session that did nothing, and the log is what lets this project be rebuilt.

- **The operator's words, verbatim** — `ddflow_session_prompt` for each instruction you
  are given. Not a summary. `ddflow_replay` reconstructs the project from these, and a
  paraphrase written afterwards reconstructs the paraphrase.
- **A decision, the moment it is settled** — `ddflow_decision_add`, with `globs` set to
  the code it governs so the next agent to touch those files is told, and
  `alternatives` set to what you rejected and why. Without that, the next agent
  re-proposes it.
- **A bug, when you find it, BEFORE you fix it** — `ddflow_bug_found`.
  `ddflow_bug_fixed` then refuses to close it without naming the regression test, so a
  bug that was never opened is a fix that never had to prove itself.
- **A lesson, after any surprise or any correction from the operator** —
  `ddflow_lesson_add`. Write the transferable rule, not the incident.
- **Research, with its verdict** — `ddflow_research_add`, `CONFIRMED` / `REFUTED` /
  `THEORETICAL`. A verdict with no probe behind it is an opinion; say so by labelling it
  `THEORETICAL`.
- **Gate outcomes with their evidence** — the command you ran and its exit code. A gate
  in `gates.evidence_required` rejects a bare "it passed".

A tool or reviewer that could not run is recorded `unavailable`, **never** `passed`.
Exit code 2 means "could not run / nothing to do" — it is a result, not an error, and
never a success.

**Your services stay on this machine.** Reviewer endpoints, private model names, API-key
variable names, hosts and worker counts sized to this box go to the git-ignored
`.ddflow/local/` layer (read last, so it wins): `ddflow_configure` with `local=true`,
`ddflow_reviewers_detect` with `write=true`. The committed config is generic project
policy. Recommend a service to the operator; never commit their setup.

## Tools this workflow expects you to use

ddflow imposes the order and demands the evidence. It does not perform the judgement
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
- **ddflow's own memory** — `rules`, with nothing to install. Record an operational
  fact about *this machine and working state* with `ddflow_memory_add`; `ddflow_brief`
  shows the newest and `ddflow_recall` searches them beside the decisions, lessons,
  research, bugs and past prompts. OptMem (`memo`) and the memory MCP server did this
  job first; they are opt-in now, for a project that wants a separate
  store, and not something to propose installing.
- **sequential-thinking** — `research`, `rubber_duck`, `bug_hunt`. The three gates that
  are reasoning rather than tool-running. Use it where a chain has middle steps you
  expect to RETRACT: a falsifiable claim and the probe that would kill it, or a bug
  hunt's competing causes. In a plain transcript a retraction is just one more
  assertion, and what you ruled out disappears — which is how an early, plausible-wrong
  cause survives to the end.

**Your stack needs servers this list cannot know about.** `ddflow_prompts` with
action `show` and name `research-companions` walks you from this pipeline's *uncovered* gates, through the
repository's actual manifests, to candidates checked against their primary sources, and
produces `[[companion]]` blocks the operator can read and delete. Propose; never install.

{% if actionable_companions %}
### Not wired up here — propose it to the operator

Each line says what is KNOWN about it. "not checked" is not "missing": the handshake
does not probe, because a session start must not wait on `npx`.

{% for c in actionable_companions %}
- **{{ c.id }}** — {{ c.title }} [{{ c.state_word }}]. Serves: {{ c.gates_text }}. Install: `{{ c.install }}` — {{ c.url }}
{% endfor %}

**This is something to DO, not just to know.** Early in the session — before you reach a
gate that needs one — tell the operator which are missing, what each one buys the gates
it serves, and exactly what installing it would run on their machine. Then:

0. **If it says "not checked", check first.** `ddflow_companions` probes and reports
   each as installed, missing, or could-not-tell. Proposing to install something that
   is already there wastes the operator's attention, and treating "not checked" as
   "missing" is the same mistake this pipeline refuses everywhere else.
1. **If they agree**, run the install command yourself, then register it IF it is an
   MCP server. **`ddflow_companions_add` with `dry_run=true` first** — it reports the
   exact config entry it would write, and writes nothing. Show the operator THAT, not
   your description of it, then call it again without `dry_run` once they say yes.
   Registering changes which processes their agent launches, and that is their decision.
   A `cli` companion is a tool you shell out to — there is nothing to register, and
   `ddflow_companions_add` will refuse it and say so.
2. **If they decline, or do not answer**, carry on — and when you reach a gate that
   companion serves, record it `unavailable` with the reason. Never pass it on your own
   unaided word: a gate with nothing behind it is the failure this pipeline exists to
   prevent, and it is invisible unless you say so.
3. **Do not install anything without asking.** ddflow deliberately never installs on
   its own; running an install command on someone's machine is the operator's decision,
   and yours is to make the case for it clearly enough that they can decide.
{% endif %}
{% if rules_drift %}
### This project's rules file needs the operator

{% for r in rules_drift %}
- {{ r.detail }}
{% endfor %}

`AGENTS.md` is what tells an agent it must claim an item before editing, and every
coordination guarantee here rests on that. Adoption is judged by `.ddflow/config.toml`,
which exists — so nothing else will report this as a problem.

**Ask the operator before fixing it.** It is a file in their repository, very possibly
with their own prose around the managed block, and rewriting it is not a decision a tool
gets to make on their behalf. Show them which file and what is wrong, then — if they
agree — call `ddflow_setup`, which replaces only the block between the DDFLOW markers and
leaves everything else untouched.

{% endif %}
{% if gate_gaps %}
Gates in this project's pipeline with no tool behind them:{% for g in gate_gaps %} `{{ g }}`{% endfor %}.
Several of those are judgement you do directly — that is fine and expected. It is the
list to re-read when a gate has been passing suspiciously easily.
{% endif %}

{% if queue_is_empty %}{% if importable %}
## This project has history, and the queue is empty

{{ importable }} file(s) that usually hold a project's work were found — a todo
checklist, a lessons corpus, architecture decisions, a research log, an engineering
journal, a cross-session memory store — and nothing is in the queue yet.
An empty queue will tell you "nothing is in flight" about a project that may have
several things in flight, and you will believe it.

Call `ddflow_import` (it writes nothing) and put what it found to the operator. The
`import-existing-project` prompt walks through the half that needs judgement: which
open items are actually live, what the branches mean, which lessons still apply, and
the globs and dependencies nobody wrote down. If the project's rulebook still tells
agents to write those files, the `onboard` prompt also cuts the workflow over and
verifies it — offer it rather than importing alone.
{% endif %}{% endif %}
{% if imported_no_globs or imported_shipped_drift %}
## The import here was never finished

This project's history was imported — {{ imported_total }} item(s) and record(s) carry
a source — but the half that needs a person was left undone:
{% if imported_no_globs %}
- **{{ imported_no_globs }} imported task(s) declare no globs.** The conflict detector
  cannot protect a task that has not said what it writes, so two agents can be handed
  the same file and neither will be refused.
{% endif %}{% if imported_shipped_drift %}
- **{{ imported_shipped_drift }} phase(s) claim the work shipped** while a task under
  them is still open. If the heading is right, this queue is about to hand out work
  that is already done.
{% endif %}
Run `ddflow_import_verify` before you hand out any imported work. It reports the full
picture, including whether the source files have moved on since. Both of those are
judgement calls: put them to the operator rather than resolving them yourself.
{% endif %}

## Before you start anything non-trivial

`ddflow_recall` — one search across every decision, lesson, research verdict, past bug,
similar task and earlier operator prompt. It exists so the operator does not have to say
the same thing twice and you do not have to learn the same lesson twice.

{% if recoverable %}
## Waiting for you right now

{{ recoverable }} worktree(s) from a crashed agent hold work that exists nowhere else.
Call `ddflow_recover` and deal with them BEFORE picking up anything new. Nothing is
ever stolen or deleted automatically, which is why it is still there.
{% endif %}
{% if loops %}
## The queue is looping

{{ loops }} loop finding(s) are open — repeated claims, a flapping gate, work that will
not stay done, or a dependency ring. `ddflow_loops` names each with its evidence and
its remedy. A ring is broken by removing one edge: `ddflow_update <id> --needs ""`.
{% endif %}
{% if setup_todo %}
## Setup still needed

{% for t in setup_todo %}- {{ t }}
{% endfor %}
{% endif %}
{% else %}
This repository does not use ddflow yet.

**If the user wants ddflow to manage this project, offer the `onboard` prompt** (your
client lists it as a slash command; `ddflow_prompts` with `action: show`, `name: onboard`
fetches it otherwise). It is the whole path in one conversation with the operator:
what is already in flight in git, setup, a measured test gate, importing the project's
history, cutting its rulebook over from its old todo, lessons and journal files, freezing
those, and an end-to-end check. The steps below are the same path without the
judgement, for a project with nothing to carry over.

If the user wants a managed work queue — phases and tasks with dependencies, parallel
agents in isolated git worktrees, a quality pipeline that refuses to pass a step nobody
ran, and crash recovery — call `ddflow_setup` ONCE. It creates `.ddflow/`, writes the
driver, adds a short section to `AGENTS.md` describing how work is claimed here, and
reports which companion tools (roborev, codeguide-mcp, context7, a memory server) are
present on this machine and which are missing.

Then set the project's test command with `ddflow_configure`, wire up what
`ddflow_companions` reports missing, and add work.

What is committed is generic project policy (`.ddflow/config.toml`, `.ddflow/gates.toml`).
The operator's own services — reviewer endpoints, private model names, API-key variable
names, LAN hosts, a worker count sized to this machine — belong in the git-ignored
`.ddflow/local/` layer: `ddflow_configure` with `local=true`, and
`ddflow_reviewers_detect` with `write=true` writes there by default. Recommend services;
never commit someone's setup.

**If this project is not brand new, call `ddflow_import` before adding anything by
hand.** It reads the todo checklists, lessons, ADRs, research log, engineering journal,
cross-session memory store and unmerged branches that are already there and proposes
them, so the queue starts where the project actually is rather than empty. It writes
nothing until `apply` is true, and it will tell you what it could NOT decide — headings
that claim the work shipped over unticked boxes, dependencies pointing at ids nothing
defines. Put those to the operator rather than resolving them yourself.

Do not call the other tools before `ddflow_setup`; they will report that there is no
queue.

If the user has not asked for any of this, say nothing about it and carry on.
{% endif %}
