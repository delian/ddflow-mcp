# Orchard

A portable, agent-agnostic **work-queue kernel** for AI coding agents.

You keep a queue of phases and tasks with declared dependencies. You say *"implement
phase P2"*. Independent tasks fan out to parallel agents in isolated git worktrees;
dependent ones wait. Every task passes a quality pipeline whose gates cannot be passed by
assertion. If an agent crashes, its work is found rather than lost. If everything except
the log is destroyed, the project's decision history rebuilds from the log alone.

**No dependencies beyond `python3` and `git`.** Works with Claude Code, Gemini CLI,
Codex, Copilot, Kilo/Cline, a CI job, a Makefile, or a human at a terminal — over a CLI
and an MCP server that are the same implementation.

---

## Table of contents

- [Why it is built this way](#why-it-is-built-this-way)
- [Install into any project](#install-into-any-project)
  - [Docker — for operators with no Python toolchain](#docker--for-operators-with-no-python-toolchain)
  - [Extending it by writing text, not code](#extending-it-by-writing-text-not-code)
  - [Publishing and registry](#publishing-and-registry)
  - [Any LLM as a reviewer — local, remote, SaaS, or a CLI](#any-llm-as-a-reviewer--local-remote-saas-or-a-cli)
  - [Companion MCP servers](#companion-mcp-servers)
- [The model: phases, tasks, dependencies, globs](#the-model-phases-tasks-dependencies-globs)
- [Work that changes shape while you do it](#work-that-changes-shape-while-you-do-it)
- [Architectural decisions](#architectural-decisions)
- [Recall — "have we been here before?"](#recall--have-we-been-here-before)
- [Status, progress, and loops](#status-progress-and-loops)
- [The task pipeline](#the-task-pipeline)
- [The phase pipeline](#the-phase-pipeline)
- [Parallelism and coordination](#parallelism-and-coordination)
- [Crash recovery](#crash-recovery)
- [Reconstruction from logs alone](#reconstruction-from-logs-alone)
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

## Why it is built this way

**The append-only event log is the source of truth; everything else is a projection that
can be deleted and re-derived.** The SQLite index, the markdown boards, the search index,
the recovery bundle — all disposable, all rebuilt by `orchard rebuild`.

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

```json
{ "mcpServers": { "orchard": { "command": "uvx", "args": ["orchard-mcp"] } } }
```

`uvx` fetches and runs the published package in an ephemeral environment on first use —
no clone, no virtualenv, no `PYTHONPATH`, no install step for an operator to forget, and
no vendored copy to drift from upstream. Orchard has **zero runtime dependencies**
beyond `python3` and `git`, which is what lets it install inside sandboxes, CI images
and other tools' ephemeral containers.

Then, from the agent, with no shell at all:

| Call | What it does |
|---|---|
| `orchard_setup` | creates `.orchard/`, writes the driver and the `AGENTS.md` section |
| `orchard_configure` with `toml: '[gate.unit_tests]\ncommand = "pytest -q"'` | sets your test command |
| `orchard_reviewers_detect` with `write: true` | finds a local model server and registers it as a cross-family reviewer |
| `orchard_phase_add`, `orchard_task_add` | fill the queue |
| `orchard_brief` | start every session here |

That is the whole adoption. **The per-project instruction text is 232 words** — a
managed block in `AGENTS.md`, because the MCP tool descriptions already carry the
how, and a second copy of that would drift from the one the model actually reads.

<details><summary>Shell / CI installation, and running from a source checkout</summary>

```sh
uv tool install orchard-mcp        # or: pipx install orchard-mcp
cd /path/to/your/project
orchard adopt --agents claude,gemini,codex,copilot,kilo
```

`adopt` is idempotent and writes managed blocks, so re-running after an upgrade updates
them and leaves your own prose alone. It writes the MCP registration into each agent's
own config location, **merged** with whatever servers are already there. From a source
checkout it points the config at that checkout instead of the published package, so
developing Orchard does not silently configure your project against the released
version.

</details>

### Docker — for operators with no Python toolchain

```json
{ "mcpServers": { "orchard": { "command": "docker", "args": [
    "run", "-i", "--rm",
    "-v", "${workspaceFolder}:/repo",
    "--add-host=host.docker.internal:host-gateway",
    "ghcr.io/OWNER/orchard:latest" ] } } }
```

`orchard adopt --launch docker` writes exactly that. The image is **107 MB** (Alpine;
Orchard is pure standard library, so there is no compiled dependency to worry musl
about) and behaves identically on Linux, macOS and Windows.

Four things go wrong when a containerised tool touches a bind-mounted git repo. All
four are silent, one of them loses work, and all four are handled:

| Trap | What it looks like | Handled by |
|---|---|---|
| **Worktrees land outside the mount** | `worktree.root` defaults to `../.orchard-worktrees`, a sibling of the repo. In a container only the repo is mounted, so worktrees go to the ephemeral layer and **are destroyed on exit with the agent's uncommitted work inside them.** | `container.default_worktree_root` relocates a sibling root to `.orchard-worktrees` inside the repo, and `adopt` gitignores it |
| **Root-owned files** | On a Linux bind mount the operator needs `sudo` to edit their own project afterwards | the entrypoint reads the mount's uid/gid and `su-exec`s down to it |
| **git refuses the mount** | "detected dubious ownership", surfacing as an unexplained Orchard failure | `safe.directory` set in the entrypoint |
| **No git identity** | `git commit` fails with "Please tell me who you are" | entrypoint prefers `GIT_AUTHOR_*`, then the repo's own config, then a clearly-marked placeholder |

And one that cannot be fully handled, so it is reported: **`127.0.0.1` inside a
container is the container.** A model server on your own machine is not reachable from
there. Orchard rewrites loopback reviewer URLs to `host.docker.internal`, and `orchard
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
orchard prompts list              # where each template currently comes from
orchard prompts eject             # copy the shipped ones into .orchard/prompts/
$EDITOR .orchard/prompts/review_system.md
```

**Including the one the agent actually reads first.** `mcp_instructions.md` is the block
an MCP client injects into the model's context on connect — the workflow, the reporting
duties, and which companion tools to reach for. It is the file to edit when you want
this project to work differently:

```sh
orchard prompts eject mcp_instructions
$EDITOR .orchard/prompts/mcp_instructions.md      # or [prompts] mcp_instructions = "..."
```

It renders against the live state — `adopted`, `task_pipeline`, `setup_todo`,
`companions`, `missing_companions`, `gate_gaps`, `recoverable`, `loops` — so the
instruction is the next concrete action rather than a fixed blurb the model learns to
skip. A broken override **says so in the instruction block itself** instead of falling
back to the default: this is the one surface where nobody would ever notice their edit
was not live.

Templates render with **Jinja2 when it is installed, and a strict standard-library
renderer otherwise** — Orchard cannot require Jinja without losing zero-dependency
installability, but a project that already has it gets the full language. The shipped
templates use the subset both engines agree on, and a test renders each one through
both and asserts the output matches, so a project that installs Jinja2 never silently
gets different prompts from one that does not.

Both renderers are **strict about undefined variables**: a prompt silently missing the
diff it was supposed to carry is the vacuous review in template form — the model
dutifully reviews nothing and reports no findings.

The rest is TOML: gates and their pipelines (`[gate.*]`, `gates.task_pipeline`),
reviewers (`[[reviewer]]`), companions (`[[companion]]`), enforcement (`[enforce]`),
cadences, and the rest of the 58 knobs.
`orchard config --set <key> <value>` edits one key in place, preserving comments.

### Publishing and registry

`server.json` carries the [MCP registry](https://modelcontextprotocol.io/registry/quickstart)
manifest (`io.github.OWNER/orchard`, PyPI package `orchard-mcp`, `runtimeHint: uvx`),
and `.github/workflows/publish.yml` publishes to PyPI and the registry on a version tag
using OIDC trusted publishing — no stored tokens. The workflow refuses to publish when
the tag, `pyproject.toml` and `server.json` disagree about the version, and
`tests/test_packaging.py` pins the same invariant locally.

### Any LLM as a reviewer — local, remote, SaaS, or a CLI

The `critic` and `rubber_duck` gates are **run by Orchard, not claimed by the agent**.
Point them at whatever you have:

```sh
orchard reviewers presets            # 19 ready-made provider settings
orchard reviewers add --preset ollama --model qwen3:8b
orchard reviewers detect --write     # probe local ports and register what is serving
orchard reviewers test               # send a known-buggy diff, check the reply
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
> empty completion and never as a clean review.

### Companion MCP servers

Orchard imposes the order and demands the evidence. It does not *perform* the judgement
inside most of its gates: `standards` wants an automated standards review, `research`
wants documentation to check a claim against, `rules` wants memory of the last time
somebody hit this. A project that installs Orchard and stops has those gates wired to
nothing — and because an agent gate passes on an assertion, that gap is invisible in
exactly the way the rest of this design exists to prevent.

So the gap is **named**:

```console
$ orchard companions
Companion MCP servers

  [x] context7   Current library documentation
       gates: research, standards
       registered for: claude, cursor
  [+] roborev    Automated second-opinion code review
       gates: standards, bug_hunt, dedupe
       installed (roborev 0.9.1) but no agent is configured to launch it.
       -> orchard companions add --id roborev
  [ ] codeguide  Language and framework coding standards
       gates: standards
       not here: codeguide-mcp is not on PATH
       -> npm install -g codeguide-mcp

Gates in this project's task pipeline with no companion behind them:
  rules, implement, rubber_duck, critic, unit_tests, bug_hunt, dedupe, merge
```

Three states, reported separately because the remedies differ: **registered**,
**installed but not wired up** (one command away), **not installed** (with the command
and the URL). `orchard adopt` prints the same summary, so the gap is visible at
adoption rather than discovered six tasks later. Exit 2 when a default companion is
missing — "no data", never collapsed into "no problem".

| | Serves | Why |
|---|---|---|
| **roborev** | `standards`, `bug_hunt`, `dedupe` | Cross-file duplication analysis, which is the failure mode of agent-written code specifically: an agent changing replicated logic reliably updates one copy and misses the rest |
| **codeguide** | `standards` | Checks against a written standard instead of the reviewer's taste |
| **context7** | `research`, `standards` | A model's memory of a library's API is exactly the kind of claim that is cheap to check and often wrong |
| **memory** | `rules` | Operational facts about *this machine* — Orchard's own `recall` covers the project's memory, which is a different thing and belongs in the committed log |

**Orchard never installs anything itself** — running an install command on someone's
machine is the operator's decision. What it does instead is *instruct the agent to ask*:
the MCP instruction block lists each missing companion with the gates it serves and the
exact command that would install it, and tells the agent to put that to the operator
early, install it if they agree, and record the affected gates `unavailable` if they
decline. Never on its own word.

`companions add` also refuses to register a server that is not present: that writes a
launch command which fails mid-task, at the moment a gate told the agent to reach for
it. Detection is read-only and bounded — and when it has not run, the state is reported
as **unknown**, not as absent. `orchard companions` probes; the MCP handshake does not,
because making an agent wait on `npx` before it can do anything is the wrong trade.

Adding a fifth is a TOML block in `.orchard/companions.toml`, not a patch:

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

## The model: phases, tasks, dependencies, globs

```
Plan ──► Phase ──► Task
```

A **phase** is a unit of *review*: its own research, its own whole-suite test pass, its
own live smoke run, merged as one coherent feature. A **task** is a unit of *execution*:
one agent, one worktree, one pipeline, one merge. Both carry `needs` (dependencies, which
may cross phases) and `globs` (the files they will write).

```sh
orchard phase add P2 --title "Billing" --needs P1
orchard task add P2.T1 --phase P2 --title "invoice model"  --globs "src/billing/invoice.py"
orchard task add P2.T2 --phase P2 --title "tax rules"      --globs "src/billing/tax.py"
orchard task add P2.T3 --phase P2 --title "checkout wiring" --needs "P2.T1,P2.T2" \
                                                            --globs "src/checkout/*"
```

**Declare globs.** They are what lets two agents work at once safely. A task with no
declared globs is a task the conflict detector cannot protect.

**Dependencies are inherited.** A phase is never claimed — only its tasks are — so
`P2 needs P1` has to govern everything *inside* P2, or it governs nothing that anyone
picks up. The readiness rule therefore consults an item's ancestors as well as itself:

```console
$ orchard next
Ready (1 ready, 0 running, 1 blocked):
  P1.T1  money
  (blocked) P2.T1: deps — phase P1 has 3 open task(s) (inherited from P2)
```

The refusal names *where* the dependency came from, because an operator told only
"P2.T1 needs P1" goes looking for a declaration that is not written there. The one
dependency **not** inherited is one pointing into your own subtree: an umbrella that
declares a dependency on its own child would otherwise make the child wait for itself,
turning a plan typo into a permanent hang.

`orchard claim` asks the *same* predicate `orchard next` does. They used to disagree —
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
orchard task add P1.T1a --parent P1.T1 --globs "src/parse.py"
orchard split P1.T1 --into "P1.T1a=parse input" --into "P1.T1b=write records"
```

`split` works **in place**: the original keeps its id, its lease history and everything
recorded against it, and becomes an *umbrella* that completes when its children do.
Closing it and opening two new ones instead would lose the thread between what was
planned and what happened — which is exactly what `orchard replay` needs.

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
orchard decision add --title "Storage is SQLite with WAL" \
  --decision "One file, WAL mode, BEGIN IMMEDIATE for writes." \
  --context "Three call sites were each opening their own connection." \
  --alternatives "Postgres — rejected: no server allowed in this deployment." \
  --globs "src/storage/*" --by operator
```

**`--globs` is what makes a decision consulted rather than merely filed.** `orchard
brief` and `orchard decision applicable <item>` surface the decisions governing an
item's declared files automatically — the agent does not have to suspect they exist.

Decisions are never edited or deleted. A reversal is a *new* decision naming the old
one (`--supersedes`), so the history of how the architecture got here survives, and a
superseded decision is shown with a pointer to its replacement rather than silently
withheld.

## Recall — "have we been here before?"

```sh
orchard recall "how should durations be represented"
```

One search across **everything the project remembers**: architectural decisions,
lessons, research verdicts, past bugs, similar tasks, and the operator's own earlier
prompts. Results are labelled by kind, because a binding decision, a transferable lesson
and a prompt from three weeks ago should change what you do in different ways.

It exists so the operator does not have to say the same thing twice and the agent does
not have to learn the same thing twice. Both failures are invisible in the moment and
obvious in the log.

## Status, progress, and loops

```sh
orchard status      # what is done, in flight, ready, blocked — one answer
orchard progress    # attempts, hours held, gate runs, commits, per item
orchard loops       # circular references and runtime loops (exit 2 = none)
```

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

Every threshold is a `[loops]` knob, and `on_detect = "block"` makes `orchard claim`
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
| 10 | `merge` | Orchard | Land it, from the primary checkout, with no checkout |

Four things are enforced rather than requested:

**Silence is not a pass.** Every gate in the pipeline must carry *some* outcome before
an item completes — passed, failed, unavailable, partial, or an explicit
`orchard gate skip <id> <gate> --reason "..."`. Without this, `gates.required` held only
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

**Reviewer independence is checked, and an unidentified reviewer establishes nothing.**
Same-family reviewers share the author's blind spots, so their agreement measures shared
priors rather than correctness. `complete` refuses unless one reviewer came from a
different pretraining family — and a reviewer whose model is not in `[agent].families`
counts as *unknown*, never as *different*. (It used to count as different: `gate record`
defaults the reviewer to the agent id, so a `standards` gate recorded with no `--model`
arrived as family "host-12345", compared unequal to "anthropic", and satisfied the
independence requirement on its own.)

```console
$ orchard complete P1.T1 --model claude-opus-5
cannot complete P1.T1 — 1 unmet condition(s):
  - reviewer independence not satisfied: every reviewer (rubber_duck) was family
    'anthropic', the same as the author. Same-family agreement is not independent evidence.
```

Every unmet condition is listed **at once** — a refusal that reveals one problem at a
time trains an agent to reach for `--force`.

---

## The phase pipeline

```
research → [ task, task, task … ] → unit_tests → bug_hunt → dedupe
         → live_test → corrections → merge
```

`live_test` is the one most often skipped and the one most worth keeping: **a green unit
suite and a working feature are different claims.** Run the real thing on a small input
and paste what it printed.

---

## Parallelism and coordination

```console
$ orchard next --phase P1
Ready (3 ready, 0 running, 1 blocked):
  P1.T1  persistent store
      writes: shortener/store.py, tests/test_store.py
  P1.T2  base62 encoder
      writes: shortener/encode.py, tests/test_encode.py

These are independent — run them in parallel worktrees.
  (blocked) P1.T3: deps — P1.T1 is open; P1.T2 is open
```

`orchard claim <ID>` leases the item and creates its worktree. A second agent is refused,
and told what to take instead:

```console
$ orchard claim P1.T4 --agent gamma
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

---

## Crash recovery

An agent is killed. Nothing is cleaned up, because in a real crash nothing runs.

```console
$ orchard recover
1 recoverable situation(s); 1 may contain work:

!! P1.T1  [expired_lease]  was: delta
     worktree /repo/../.orchard-worktrees/P1.T1
     INSPECT FIRST — 1 uncommitted file(s), 1 unmerged commit(s).
     `git -C .../P1.T1 diff main` then salvage,
     then `orchard release P1.T1 --note salvaged`.
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
$ orchard replay --out ./recovery-kit
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

## Lessons, research and bugs

```sh
orchard lesson add --title "Truncating a slug can leave a trailing separator" \
                   --rule "Strip separators AFTER slicing to length, not before."
orchard lesson search "cutting a url short leaves a dangling hyphen"
```

Retrieval is BM25 over FTS5 and finds that entry despite no shared keyword. Probed
against embeddings and found sufficient at lesson-corpus scale ([R4](docs/RESEARCH.md));
`lessons.search_backend` exists for when that stops being true.

Research entries **must** carry a verdict, and `CONFIRMED`/`REFUTED` are refused without
a probe:

```console
$ orchard research --question "is it fast?" --verdict CONFIRMED
CONFIRMED requires a --probe (and ideally --probe-output): a verdict with no probe behind
it is an opinion. Use THEORETICAL and say why no probe was possible.
```

And a bug cannot be closed without the test that would catch it again:

```console
$ orchard bug fixed B1
a bug may not be closed without --regression-test naming the test that would catch it
again. Write the test, watch it FAIL against the unfixed code, then close.
```

That refusal is the whole mechanism by which the same bug does not ship twice.

---

## Cadences

Periodic whole-repo passes a per-task gate structurally cannot do. Due-ness is **derived
from completed work**, so there is no state file to drift:

```console
$ orchard cadence
DUE: integration_tests — 5 tasks since last (every 5)
DUE: mutation_tests — 3 phases since last (every 3)

Record one with: orchard cadence --ran <name>
```

Configurable: integration tests, architecture review, mutation testing, duplication
sweep, lessons compression.

---

## Keeping session-start cost flat

```sh
orchard brief --phase P2
```

Returns, inside `session.brief_max_tokens` (default 1200): recoverable work first, then
the current item and its remaining gates, then what is ready, then why everything else is
blocked, then the handful of past lessons **ranked against this task's text**.

This *replaces* reading the project's rule and lesson corpora. The budget is enforced by
truncating from the bottom, so the safety-critical head survives a squeeze — and a
project's opening cost stays roughly constant as its lesson corpus grows.

---

## Agent portability

One canonical driver, [`templates/drivers/implement-phase.md`](templates/drivers/implement-phase.md),
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
| every CLI **subcommand** has a tool | `orchard gate skip` and `bug found` had none — `gate` counted as "covered" by `gate run`, and a parent's coverage says nothing about its children |
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
orchard adopt [--agents ...]     install into a project, for one or more agents
orchard init                     create .orchard/ only

orchard phase add <id> [...]     add a phase
orchard task add <id> --phase .. add a task
orchard update <id> [...]        change title/body/needs/globs/tags/priority

orchard next [--phase P]         what may start now       (2 = nothing actionable)
orchard claim <id> [--globs ..]  lease + create worktree  (3 = refused)
orchard heartbeat <id>           renew a lease
orchard release <id>             give it up

orchard gate status <id>         pipeline position + the next gate's instruction
orchard gate run <id> <gate>     execute a command gate, record its evidence
orchard gate record <id> <gate>  record an agent gate    (--outcome, --reason, --model)
orchard gate skip <id> <gate>    skip, with a mandatory reason

orchard merge <id>               merge from the primary checkout, no checkout
orchard complete <id>            finish        (3 = unmet conditions, all listed)
orchard block <id> --reason ..   mark blocked

orchard brief [--item|--phase]   budgeted session-start pack
orchard board / show <id>        human views
orchard render                   regenerate docs/orchard/*.md

orchard lesson add|search        capture and retrieve lessons
orchard research --verdict ..    record a finding (probe required for CONFIRMED/REFUTED)
orchard bug found|fixed          regression test required to close

orchard session start|prompt|note|end     provenance logging
orchard replay [--out DIR] [--verify]     reconstruct from the log

orchard recover [--apply]        find crashed agents' work   (2 = nothing)
orchard doctor                   integrity + health
orchard rebuild                  re-derive the index
orchard cadence [--ran NAME]     which periodic passes are due  (2 = none)
orchard config --explain         every knob, its value, its source and its docs
orchard config --append-toml ..  add config without a shell editor (validated first)
orchard reviewers detect|list|test   find and check cross-family review endpoints
orchard review <id> --gate ..    run the configured reviewer, record the evidence
orchard mcp                      run the MCP stdio server
```

---

## Configuration

58 knobs across 11 sections, every one documented in place:

```console
$ orchard config --explain --filter lease
lease.ttl_s = 1800   [default]
    Seconds a lease stays valid without a heartbeat. After this it is EXPIRED and
    reclaimable. Longer = fewer false expiries when an agent is deep in a slow gate;
    shorter = faster recovery after a crash.
```

Resolution: dataclass defaults → `.orchard/config.toml` → `ORCHARD_<SECTION>_<KNOB>` env.
An unknown knob is an **error**, never a silent drop. A test asserts every knob carries
documentation, so the reference cannot rot.

---

## Testing

```sh
python3 -m pytest tests/ -q          # 423 unit/integration tests
python3 demos/run_all.py             # 6 end-to-end scenarios, 219 assertions
```

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
| [docs/RESEARCH.md](docs/RESEARCH.md) | Ten research questions with probes, measured output and verdicts; the self-found bug catalogue, including the 2026-09-24 review pass (R10) |
| [docs/RECOVERY.md](docs/RECOVERY.md) | Operator runbook: crashes, corruption, divergence, full reconstruction |
| [templates/drivers/implement-phase.md](templates/drivers/implement-phase.md) | The canonical agent-agnostic driver |
| [templates/drivers/deltas/](templates/drivers/deltas/) | Per-agent deltas: Claude, Gemini, Codex, Copilot, Kilo |
| [probes/](probes/) | Runnable probes behind the research verdicts |
