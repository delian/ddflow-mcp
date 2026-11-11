# Adopting ddflow: install, wiring and existing history

Installing, Docker, wiring ddflow into an agent, adopting a project that already has history, keeping AGENTS.md true and what to do when a companion is missing. Part of the ddflow [README](../../README.md); the reference moved out of it by D-readme-split. Every heading here keeps the anchor it had in the README.

## Install into any project

**One line in your agent's MCP config. Nothing else.**

> Until the first release is on PyPI, `uvx ddflow-mcp` has nothing to fetch: install from
> the repository and run `ddflow adopt`, which registers that installation instead, as in
> [A first run](../../README.md#a-first-run).

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

`uvx` fetches and runs the published package in an ephemeral environment on first use —
no clone, no virtualenv, no `PYTHONPATH`, no install step for an operator to forget, and
no vendored copy to drift from upstream. ddflow needs **two runtime dependencies**
beyond `python3` and `git` (Jinja2 and tomlkit, both pure Python), which is what lets it install inside
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
adopted agent's delta) that differs from the template the running ddflow ships.
`ddflow adopt --refresh-docs` (MCP: `ddflow_setup` with `refresh_docs`) rewrites
only those docs, the AGENTS.md/CLAUDE.md blocks and the agents' native rules -- never the
MCP launch, hooks or command files -- and refuses a project that was never adopted.

**Stale references.** A file the project holds can name a command or tool that a release
renamed: a settings hook, a git hook, `.pre-commit-config.yaml`, a driver doc, an AGENTS.md or
CLAUDE.md block, a slash command, an ejected prompt, a macro. `ddflow doctor` (and
`ddflow_doctor`; an MCP server has no command parser loaded, so it checks tool names and
says in one note how many command references it could not check; the CLI's `doctor` checks both) resolves every `ddflow <command>` (in backticks, a fenced block or a
command line; prose is not read) and `ddflow_<tool>` they name against this ddflow: *ok*,
*deprecated* (an alias, with the replacement), *unknown*, or *unchecked* where the process has no table to judge it by, each with `file:line`. A
deprecated name, or an unknown one in the project's own text, is a note; an unknown name in a
region ddflow wrote and nobody edited is a problem, because the command it runs will fail.
Text inside a ddflow-managed region is rewritable (`services.compat_refs.rewrite`: backed up
first, the region re-stamped); anything else is the project's own and only gets a proposal,
never a silent edit. A hook entry in a settings file is ddflow's by its stamped region
(`hooks/<name>`), not by the command it runs, so renaming a `ddflow hooks ...` subcommand
refreshes the installed hook in place instead of adding a second one; an entry written before
the stamp is still found by its command text.

A driver doc is a stamped region (`<!-- ddflow:begin drivers/implement-phase ddflow=V fmt=N
sha=H -->` ... `<!-- ddflow:end drivers/implement-phase -->`): the version that wrote it, its
format level and a digest of its body. That is how a refresh and `ddflow upgrade` tell an
unedited copy from an older release (replaced) from a **hand edit** (the digest no longer
matches). A hand edit is never silently overwritten: before the refresh rewrites the file,
your version is copied whole to `<file>.local-edits` (`.local-edits.2`, `.3` ... when that
name is taken) and the action says so; text you wrote outside the region stays where it is,
and the originals also go to `.ddflow/backups/`. The same holds for an edit inside the
AGENTS.md/CLAUDE.md block, and for the `/implement` command file when a plain
`ddflow adopt` rewrites it (a refresh never touches command files). A copy written before the
stamp (a driver an older ddflow copied) has no digest to tell by: it is replaced after a
backup, and `ddflow upgrade --plan` calls it *stale* when a release has shipped since the
project last worked and *hand-edited* (needs the operator) when none has. A doc written at
a higher format level than this ddflow understands is left alone: upgrade ddflow.

`adopt` is idempotent and writes managed blocks, so re-running after an upgrade updates
them and leaves your own prose alone. It writes the MCP registration into each agent's
own config location, **merged** with whatever servers are already there, through the same
writer `companions add` uses: an entry that already holds this launch is left untouched, a
stale `ddflow` entry is refreshed (in `.codex/config.toml` too, where a header that is only
text -- a comment, say -- no longer counts as a registration), and a config it cannot parse
is refused with "add the server by hand". From a source
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
| **Worktrees land outside the mount** | A `worktree.root` set outside the repo (such as `../.ddflow-worktrees`, the default before D-worktree-home) goes, in a container where only the repo is mounted, to the ephemeral layer, and **the trees are destroyed on exit with the agent's uncommitted work inside them.** | the default is `.ddflow/worktrees`, inside the repo and ignored by `.ddflow/.gitignore`; `container.default_worktree_root` relocates an outside root there |
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
generator, a managed `ddflow:begin rules/work-queue` block in each, and `ddflow doctor` comparing every
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

## When a companion is missing

ddflow imposes the order and demands the evidence. It does not *perform* the judgement
inside most gates — that is what the companion tools are for. So the honest question is
what happens when one is absent, and the answer is deliberately never "the gate passes".

| Companion | Serves | If it is missing |
|---|---|---|
| **roborev** *(cli)* | `standards`, `bug_hunt`, `dedupe` | Record the gate `unavailable` with the reason. A second opinion is missing and the log says so. |
| **codeguide** | `standards` | The standards gate falls back to the reviewer's taste. Still recordable — but say which it was. |
| **context7** | `research`, `standards` | Claims about a library's API rest on the model's memory, which is exactly the claim that is cheap to check and often wrong. |
| **sequential-thinking** *(opt-in)* | `research`, `rubber_duck`, `bug_hunt` | Only records and counts thoughts; benefit unmeasured. |
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
| Research | `docs/RESEARCH.md` | research notes, `THEORETICAL`: a scraped `CONFIRMED`/`REFUTED` has no probe ddflow can check, so it is kept as a `source-verdict:` tag |
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
- **Readable ids.** A phase or task id made from a heading is cut at a WHOLE WORD, never inside
  one, at `[importer] phase_slug_chars` (default 32) and `task_slug_chars` (default 40; at least
  8 each; only a first word longer than the limit is kept whole; a later word that does not fit is dropped): `Phase 106C tool-rl multi-agent run` gives `PHASE-106C-TOOL-RL-MULTI-AGENT`, where
  it used to give `PHASE-106C-TOOLRL-MULTI`. A heading that declares its own id keeps it, and a
  re-import recognises the older truncated ids (`legacy_ident`), so nothing is minted twice. Set
  the two knobs BEFORE the first import: a re-import under different values derives different
  ids and proposes those items again.
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

Every path pattern ddflow reads (claims, shared globs, `[enforce]` doc and code globs, `archive_globs`,
lesson globs) means what git means: `*` and `?` stop at `/`, `**/` is any number of directories
(including none), `[...]` is a class; one implementation, `ddflow/core/globs.py`.

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
`"off"` turns the check off. Under the shipped default, `ask`, near-duplicates are
withheld. Re-running over the same files adds nothing.

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
  replaces only what is between the `ddflow:begin`/`ddflow:end rules/work-queue` markers and leaves the rest
  of your file alone, and re-running it is a no-op. `ddflow init` **reports** the problem
  and does not write — writing prose into your `AGENTS.md` is not what `init` was asked to
  do.
- **Over MCP**, ddflow does not touch it. The handshake tells the agent to show the operator
  what is wrong and call `ddflow_setup` only if they agree. It is a file in their
  repository, usually with their own prose around the block, and rewriting it is not a
  decision a tool gets to make on their behalf — the same rule as companions ("propose;
  never install") and the human-approval gate.

---
