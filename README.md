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
- [The model: phases, tasks, dependencies, globs](#the-model-phases-tasks-dependencies-globs)
- [The task pipeline](#the-task-pipeline)
- [The phase pipeline](#the-phase-pipeline)
- [Parallelism and coordination](#parallelism-and-coordination)
- [Crash recovery](#crash-recovery)
- [Reconstruction from logs alone](#reconstruction-from-logs-alone)
- [Lessons, research and bugs](#lessons-research-and-bugs)
- [Cadences](#cadences)
- [Keeping session-start cost flat](#keeping-session-start-cost-flat)
- [Agent portability](#agent-portability)
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
reviewers (`[[reviewer]]`), enforcement (`[enforce]`), cadences, and 47 other knobs.
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

There is deliberately no third level. A sub-sub-task is representable as a task with a
cross-phase dependency, and the extra level costs more bookkeeping than it buys.

---

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

Three things are enforced rather than requested:

**UNAVAILABLE is never a pass.** A reviewer whose endpoint was down approved nothing; a
linter that is not installed found nothing. Each gets its own outcome and shows as a
coverage gap. (The inverse matters too: this codebase's first version classified a
*missing binary* — shell exit 127 — as `failed`, so an uninstalled linter looked like a
linter reporting problems. Fixed, with a mutation-verified regression test.)

**Evidence or it did not happen.** Gates in `gates.evidence_required` reject a bare pass;
they want the command, its exit code and its output digest.

**Reviewer independence is checked.** Same-family reviewers share the author's blind
spots, so their agreement measures shared priors rather than correctness. `complete`
refuses unless one reviewer came from a different pretraining family:

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

43 knobs across 8 sections, every one documented in place:

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
python3 -m pytest tests/ -q          # 234 unit/integration tests
python3 demos/run_all.py             # 5 end-to-end scenarios, 122 assertions
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

**The scenarios and the stress test have found most of the bugs this project fixed; the unit tests found few of them.** The composed MCP run alone found eight that 213 unit tests and four other scenarios missed — including two that made core features useless out of the box. They all lived in *seams*: between two processes, between a read and a write, between two output surfaces, between a declared vocabulary and its callers, including one that does
not reproduce below ~6 concurrent processes. They are catalogued with their regression
tests in [R6](docs/RESEARCH.md#r6--bugs-this-project-found-in-itself).

---

## Documentation index

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The event-log inversion, ordering, concurrency, module map, what is deliberately absent |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Six research questions with probes, measured output and verdicts; the self-found bug catalogue |
| [docs/RECOVERY.md](docs/RECOVERY.md) | Operator runbook: crashes, corruption, divergence, full reconstruction |
| [templates/drivers/implement-phase.md](templates/drivers/implement-phase.md) | The canonical agent-agnostic driver |
| [templates/drivers/deltas/](templates/drivers/deltas/) | Per-agent deltas: Claude, Gemini, Codex, Copilot, Kilo |
| [probes/](probes/) | Runnable probes behind the research verdicts |
