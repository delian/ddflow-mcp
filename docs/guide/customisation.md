# Customisation and exports

What you can change per project, the macro and prompt layers, and exporting documents. Part of the ddflow [README](../../README.md); the reference moved out of it by D-readme-split. Every heading here keeps the anchor it had in the README.

### Extending it by writing text, not code

Every prompt is an external template, resolved config → project → shipped:

```sh
ddflow prompts list              # where each template currently comes from
ddflow prompts show <name>       # its source, as eject would write it
ddflow prompts get <name> --arg KEY=VALUE   # a workflow command or macro, rendered
ddflow prompts eject             # copy the shipped ones into .ddflow/prompts/
$EDITOR .ddflow/prompts/review_system.md
```

`prompts get` renders through the same code as MCP `prompts/get` (and the `ddflow_prompts`
tool's `get`): every declared argument bound, a macro's parameters required and its `tools`
preamble on top. `show` prints the text as written, `{% if %}` tags and all; a machinery
template (`mcp_instructions`, the review prompts) takes no caller arguments, so `get`
refuses it and names `show`.

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
When the whole `[[macro]]` config cannot be read (a parse error, an unknown field), MCP
`prompts/get` for an unknown name says why (`not loaded: ...`) instead of only "unknown prompt".

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

Templates render with **Jinja2**, one of ddflow's two runtime dependencies (the other, **tomlkit**,
writes `ddflow config --set` and the other config edits in place, keeping comments, blank lines and
key order; every TOML value ddflow writes goes through the one writer in `infra/tomlcfg.py`), and with a
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
cadences, and the rest of the 675 knobs.
`ddflow config --set <key> <value>` edits one key in place, preserving comments.

Every config change goes through the one write pipeline, `services/configwrite.apply_edit`:
`config --set`, `--append-toml`, `ddflow_configure`, `workflow pipeline|gate|drop`,
`reviewers add|detect --write` and `export enable` all compose the edit, judge the RESULT once
(format, schema, plain keys, the adaptive range, `[gate.*]` tables, the human-approval gates,
and the workflow's coherence) and replace the file atomically under a lock. The same guards hold
for the committed and the local layer and for every kind of edit, so an appended block is held
to what `--set` is held to. Only a person typing on the command line writes a number or boolean
as text; a `workflow gate --command 5` is the string `"5"`. A committed write brings
`.gitattributes` up to date with `[lease] append_only_globs`, whichever command made it.

#### What is committed, and what stays on your machine

ddflow **recommends** services; it never ships one person's configuration. Two layers:

| Layer | Files | Holds |
|---|---|---|
| committed | `.ddflow/config.toml`, `.ddflow/gates.toml` | generic project policy: the test command, the pipelines, the gates (human checkpoints included) — what every clone must agree on |
| machine-local, git-ignored | `.ddflow/local/config.toml`, `.ddflow/local/gates.toml`, `.ddflow/local/reviewers.toml` | *your* services: reviewer endpoints, model names of a private deployment, API-key variable names, LAN hosts, a worker count sized to this machine |

The local files are read **last**, so they win; `ddflow config --explain` reports such a
value's source as `local`. A command gate that ran with a `command` set in the local layer
records `command_layer: "local"` (else `"committed"`), a `command_digest`, and, when the
committed layers define a different command, `committed_command_digest`; `gate status`, `show`
and `complete` then label that pass "passed with a local-only command (not the committed one)",
since it is not the project's pass. The evidence also names `output_log_host` (the agent whose
machine holds the git-ignored `.ddflow/runs` log, which a teammate's checkout does not have).
Evidence recorded before these keys is not labelled. Every writer of an operator-specific value targets the local
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

A fresh `ddflow init` says the same in the `config.toml` it writes: the commented
`[[reviewer]]` example points at `.ddflow/local/reviewers.toml` (not at `config.toml`), and a
short comment block separates what is committed (generic project policy) from what is local
(your endpoints, hosts, key variable names, machine sizing: `ddflow config --local`).

## Exporting documents

The log already knows the roadmap, the open bugs, the decisions in force and what was done
this week. `ddflow export` renders that as ordinary markdown, so someone who never runs
ddflow can read it in a pull request. ddflow owns the format (a Jinja2 template per kind,
rendered in a sandbox); you ask for a document, you do not write it.

**What can be exported:** `roadmap` (Now / Next / Later with progress), `bugs` (open, fixed,
invalid), `status`, `worklog` (coalesced per item, grouped by day), `sessions`, `decisions`
(an index), `rules` (decisions in force by governing path, lessons, the enforced workflow),
`changelog` (Keep a Changelog form, from finished items, fixed bugs and version tags) and
`security` (a GitHub-compatible `SECURITY.md`: supported versions and known issues).
Replay is not a kind (`ddflow export replay` is refused): it carries paths and addresses.
Output is deterministic: the same log gives the same bytes, with no clock in the body.

| kind | default target | filters it takes | what it holds |
| --- | --- | --- | --- |
| `roadmap` | `ROADMAP.md` | `phase` `limit` | phases in Now / Next / Later lanes, with progress bars |
| `bugs` | `BUGS.md` | `since` `limit` `status` `phase` | open, fixed and invalid bugs (`--item` is `--phase`) |
| `status` | `STATUS.md` | none | counts, agent-hours, work in flight, open bugs |
| `worklog` | `LOG.md` | `since` `limit` | one line per item per day, newest first |
| `sessions` | `SESSION.md` | `since` `limit` `session` | summary first, then prompts, notes and items touched |
| `decisions` | `DECISIONS.md` | `since` `limit` `status` | an index: id, status, date, title, what it governs |
| `rules` | `RULES.md` | `limit` `tag` | decisions in force by governing path, lessons by tag, the enforced workflow |
| `changelog` | `CHANGELOG.md` | `tag` (`--version`) | Keep a Changelog 1.1: Unreleased and one section per version tag |
| `security` | `SECURITY.md` | none | supported, deprecated and unsupported release lines; known issues from bugs tagged security |

The **roadmap** lists phases in dependency order then priority: Now is work in flight, Next
the top `next` ready phases, Later the rest (task lists capped at `tasks`, Later as counts), each
with percent, counts by status, what it waits on, its release line and whether it is on the
critical path. `[export.roadmap]` takes `hide` (id prefixes or tags, default `["BL-"]`), `next`
(3), `tasks` (8), `graph` (25; the phases the Mermaid dependency graph draws, edges implied by a longer path removed, 0 for none) `eta` (true; the metrics forecast's range per Now / Next phase, labelled an estimate and left out when there is not enough data) and `show_hidden`; `--phase` names a hidden batch to show it. `--since` (default: the latest version tag's date) adds a Changed section (phases and tasks added, completed, blocked, reprioritised since) and writes `since=` into the header; each phase lists the decisions whose globs overlap its own and the research about it.

The **security** document keeps two named regions (mode `region`) of `SECURITY.md`, or of `.github/SECURITY.md` or `docs/SECURITY.md` when the policy already lives there. Supported Versions has one row per `MAJOR.MINOR` line of the version tags: the newest line and the `[flow.lines]` maintenance lines are supported, `[export.security]` `supported_lines` adds lines (`"1.2"`, or `"1"` for a major), `deprecated_lines` marks some deprecated (it wins), and the rest are unsupported. Known Issues lists the non-invalid bugs carrying one of `tags` (default `["security", "secbug"]`), on the bug or its fix task, with the version holding the fix (by ancestry; `by date` when the fix has no landing). A release is marked `vulnerable: <id>, fixed in <version>` when it was tagged at or after the bug was found and lacks the fix; the newest release is also marked for anything no release has fixed. `disclose` decides what a row publishes beside the id and severity: `ids` (default) nothing, `fixed` the title of a fixed issue, `all` the title of an open one too, which is the operator's call. With no such bug the section says "None recorded." Dependency advisories and security-pass findings are not recorded yet, and the document says it does not cover them. The Reporting section lies outside the regions: a new file gets a TODO for the owner (ddflow never invents a contact) and it is never written again. A file with no markers is left alone until `--update --force`, which needs the operator's approval from an agent, appends the regions to its end.

Every default target is at the repo root; `[export.<doc>].path` moves it. `rules` exports
decisions in force, lessons and workflow settings: imported rulebooks are not exported. The
**changelog** takes each finished item (and each fixed bug that carries a changelog line) and
files it under Added / Changed / Deprecated / Removed / Fixed / Security by the first rule that
answers: the explicit `--changelog "Added: ..."` line given to `ddflow complete` (see
[Changelog line](../../README.md#the-default-workflow-at-a-glance)), then the Conventional Commit type of the item's landing commit, then
the item's tags (`breaking`, `feature`, `bug`, `hotfix`), then a `(fixes bug X)` title suffix, else
Changed. `--changelog skip` keeps an item out. An entry lands under the OLDEST version tag whose
history contains it, otherwise under Unreleased; one with no merge commit is placed by date and
says so. Nothing before the first version tag is reconstructed (that tag is the baseline). It
reads git as well as the log (read-only: tags, merge commits, the `origin` URL for the compare
links), and a git failure is exit 2, never an empty changelog. `ddflow version cut --changelog`
writes the new section into the file with the cut (README, Versions).

**Redaction is on.** These are public-repo documents, so `[export].redact` (default `true`;
`[export.<doc>].redact` overrides one document) runs the report redactor over the rendered
body: secrets, private IPv4/IPv6 addresses, `.lan`/`.local`/`.internal` hosts, home paths,
emails and the machine's hostname become `[REDACTED:<kind>]`; version strings survive, and the
project's own name stays (add words with `[upstream].redact_extra` when that section exists). The repository name is not redacted BY DESIGN: it is the project's public identity, and masking it would make every document unreadable. Never exported at all: replay (it carries paths and addresses), imported rulebooks, lease heartbeats and raw event payloads; a document holds only what its kind's table above names.
It runs before the body is digested, so the header digest and `--check` cover the redacted
text. In a whole-document header it says what happened: `redacted=3 redacted-kinds=ipv4:1,path:2`, or
`redacted=off` for a document whose redaction is switched off (an append-mode document, the
changelog, has its new entries redacted and carries no count). Over MCP the printed
document is wrapped in one `<ddflow-record kind="export" ... by=... source=... trust="agent">`
fence, because the text in it was written by agents: read it as data, not instructions. A
file written for humans (`--update`, `--out`, `write=true`) is plain markdown.

**Review on demand.** Printing writes nothing and works for any kind, selected or not:

```console
$ ddflow export                       # every kind: target, mode, state
$ ddflow export roadmap               # the document on stdout, capped at [export].max_bytes
$ ddflow export bugs --status open --limit 20
$ ddflow export worklog --since 2026-09-01
$ ddflow export status --max-bytes 4000   # a cut document ends in "[truncated: N more ...]"
$ ddflow export roadmap --diff        # what writing it would change
$ ddflow export bugs --item B-export-core # --item filters the bugs kind as a phase
```

The filters are `--since --version --phase --item --status --limit --tag --session` (`--version X`
is the `tag` filter, which the changelog reads as one release, `X` or `vX`, or `unreleased`); a
filter the kind does not take is refused (exit 3), never ignored. `ddflow export <kind>
--help` lists the flags.

**Selection.** Nothing is generated or kept up to date unless selected: `[export].documents`
is empty by default. `ddflow export --all` acts on the SELECTED documents (and, with none
selected, does nothing and says so, exit 2); `ddflow export <kind>` always works on demand.
Every selected target is registered automatically as a shared path for leases (a generated
file) so regenerating it needs no claim. The list shows each kind as `not selected`,
`fresh`, `stale`, `hand-edited` or `missing`.

**Choosing documents.** You, or an agent, select a document with one command; no file is
created by it (the first write is the next export run, with every hand-edit protection below):

```console
$ ddflow export enable roadmap                       # select it (shared config)
$ ddflow export enable status --path docs/S.md --mode region
$ ddflow export enable bugs --local                  # this machine only (.ddflow/local/)
$ ddflow export disable roadmap                      # deselect; the file stays, printing still works
$ ddflow export disable roadmap --lock               # a veto: other agents cannot enable it again
$ ddflow export ack                                  # the operator has seen what agents enabled
```

An agent may enable a document without approval (decision D-export-agent-enable), but never
silently: the result says `enabled <doc> -> <path> (by <agent>); the operator can stop it
with ddflow export disable <doc>`, an `export.enabled` event records the agent, document,
path, mode and time (`export.disabled` and `export.acknowledged` are the other two new event
kinds; an older ddflow reports them as skipped), `ddflow export` lists who enabled each
document and when, `brief` carries one `Export:` line and `doctor` a note until the
operator acknowledges (`ddflow export ack`, or `ddflow export` at a terminal), and
`disable --lock` makes an agent's later `enable` exit 3 (`locked by the operator`). An agent
may lock a document too (B-export-agent-lock): the `export.disabled` event records the agent's
id, another agent's `enable` exits 3 (`locked by agent <id>`), the locking agent may lift its
own lock, and the operator can always unlock (`ddflow export enable <doc>` at a terminal). An
agent's lock never overrides or replaces the operator's lock, which only a person (or an
agent spending the operator's approval, `export.unlock`) lifts. Acknowledging and unlocking
the operator's lock are the operator's acts, refused under `--agent`, `DDFLOW_AGENT` or a
harness's shell (as `reviewers approve` is). Enable edits the selection through the same guarded write as `config --set`, and an
`append` target is registered as an append-only path.

**Templates are yours to change.** The format of every kind is a Jinja2 template; resolution
is `[export.<doc>].template`, then `.ddflow/templates/export/<kind>.md.j2`, then the
shipped default.

```console
$ ddflow export eject roadmap        # copy the shipped template into .ddflow/templates/export/
$ ddflow export eject roadmap --force  # replace an edited copy with the shipped default
$ ddflow export validate [roadmap]   # render the selected documents; errors with file and line
```

`eject` is idempotent and never overwrites an edited copy without `--force`; the copy starts
with a comment (`{# ddflow-shipped: <digest> -#}`, renders to nothing) recording the shipped
text it came from, so a ddflow upgrade never touches it but `validate` and `doctor` say when
the shipped default has moved on, and an unedited older copy is refreshed by a plain `eject`.
`validate` renders every selected document (or the one named) against the current data and
exits 2 on any template error, naming the file and line; it writes nothing.

**The template contract.** A template sees ONLY the plain data of its kind (strings, numbers,
lists, dicts), plus `schema_version` and a few filters; ddflow adds fields within a
`schema_version` and bumps it only when it renames or removes one, so an edited template keeps
rendering across an upgrade. The kinds' top-level variables:

| kind | variables |
| --- | --- |
| `roadmap` | `lanes` (each: `label`, `blurb`, `phases` of `id` `title` `done` `total` `waits_on` `tasks`), `open_tasks`, `done_phases`, `hidden_phases` |
| `bugs` | `sections` (each: `name`, `label`, `total`, `hidden`, `bugs` of `id` `title` `severity` `found` `fixed` `item` `summary` `reason` `test` `age_days`), `as_of` |
| `status` | `task_counts` (`state`, `n`), `tasks_done`, `tasks_total`, `phases_done`, `phases_total`, `open_bugs`, `bugs_total`, `decisions`, `lessons`, `in_flight` (`id` `title` `holder` `state`), `agent_hours`, `as_of` |
| `worklog` | `days` (each: `date`, `lines` of `text`), `since`, `has_since`, `window_days`, `windowed`, `entries` |
| `sessions` | `sessions` (each: `id` `agent` `model` `opened` `closed` `summary` `prompts` `notes` `items_text`), `shown`, `total`, `with_summary` |
| `decisions` | `rows` (`id` `status` `date` `title` `governs`), `shown`, `total`, `live` |
| `rules` | `decision_groups` (each: `path`, `decisions`), `decisions_without_path`, `decision_count`, `lesson_groups` (each: `tag`, `lessons`), `lessons_shown`, `lessons_omitted`, `workflow`, `tag` |
| `changelog` | `sections` (each: `title`, `version`, `date`, `unreleased`, `groups` of `category` and `entries` of `id` `line` `by_date`), `links` (`label`, `url`), `baseline`, `single`, `unplaced`, `has_by_date`, `shown` |

(The authoritative list is the kind's `data()` in `ddflow/services/export/kind_<name>.py`; the
shipped template in `ddflow/templates/export/` shows each variable in use, and
`ddflow export eject <kind>` gives you a copy to read.) The filters, besides Jinja's
built-ins: `md_escape` (escape what would change rendering in free text -- emphasis markers, pipes, backticks, line-start list/heading/quote markers, tag-like `<` -- and leave identifiers such as `run_nemo_run` alone), `wrap(width)`, `date` (the `YYYY-MM-DD`
of a timestamp), `truncate(limit)` and `bar(done, total, width)` (a `[###.......]` bar).
Templates run in Jinja2's sandbox (strict undefined variables, no autoescape): no attribute
walks to `__class__`/`__globals__`, no `open`, no imports, a time limit and ceilings on output
and on one operation's size, because a template may arrive with a cloned repository. Treat the
ceilings as containment, not as a promise that a hostile template is harmless. A template error
is exit 2 naming the file and line. The generated header and the body digest are added AROUND
the template's output, so no template can remove hand-edit protection; and the redactor runs on
what a template produced, so customising cannot leak what redaction would have caught.

**Upgrade drift.** `eject` records the shipped text it copied (`{# ddflow-shipped: <digest> -#}`
on line 1). When a later ddflow changes the shipped default, your copy is left alone, and
`validate` and `doctor` print a note: `export template ... was ejected from an older shipped
default`. To pick up the new default: copy your file aside, run `ddflow export eject <kind> --force` (it replaces your copy with the shipped one), diff the two, and re-apply your edits.

**Worked example: a changelog in your own words.** Suppose release notes should name the item
behind each line, drop the "placed by date" marker, and open with a product intro instead of
the Keep a Changelog boilerplate.

```console
$ ddflow export eject changelog
ejected changelog -> .ddflow/templates/export/changelog.md.j2
$ $EDITOR .ddflow/templates/export/changelog.md.j2
```

Two edits in that file. Replace the entry line

```jinja
- {{ e.line }}{{ " _(placed by date, no merge commit)_" if e.by_date else "" }}
```

with

```jinja
- {{ e.line }} ({{ e.id }})
```

and replace the paragraph that begins `All notable changes to this project` with
`Release notes for Acme Billing.` Then check and review before writing anything:

```console
$ ddflow export validate changelog      # renders it; errors name file and line
changelog: ok (.ddflow/templates/export/changelog.md.j2)
$ ddflow export changelog | head        # the new format, on stdout
$ ddflow export changelog --diff        # what --update would change in CHANGELOG.md
$ ddflow export changelog --update
```

Because the file's digest covers its body, the next `ddflow export --all --check` calls the
old CHANGELOG.md stale until `--update` rewrites it with your format: that is the check
working, not a fault. Commit `.ddflow/templates/export/changelog.md.j2` with the project so
every clone and CI renders the same bytes. To try a template without installing it, use
`ddflow export changelog --template my.md.j2` (writes nothing). Because a `schema_version`
change is the only thing that renames a variable, a template written against version 1 keeps
working, and a variable you misspell is an error (strict undefined), never an empty line.

**The three update modes**, per `[export.<doc>].mode`: `whole` (the file is generated; its
header carries the kind, the ddflow version and a digest of the BODY, so hand edits are
detected and `--check` regenerates and compares exactly), `region` (only the text between
`<!-- ddflow:begin doc=<kind> ... -->` and `<!-- ddflow:end doc=<kind> -->` in a hand-written
file; the rest is kept byte for byte) and `append` (a kind that can grow a log, today the changelog: entries are
added after the last exported event). Writes are atomic under a lock. A hand-edited generated file,
an unmarked file or an edited region is never overwritten without `--force` (exit 3, with the
diff); paths outside the repo, symlinks and `.git` / `.ddflow` are refused. A document or region
written at a higher format level than this ddflow's (`fmt=N` in its header, written only above
level 1) is never rewritten, even with `--force` (exit 3, "upgrade ddflow"); `--check` and `--diff`
still report it.

```console
$ ddflow export roadmap --update          # write the target; asks to confirm on a terminal
$ ddflow export roadmap --out docs/ROADMAP.md
$ ddflow export --all --check             # exit 1 if any selected document is stale
$ ddflow export --all --update --yes
$ ddflow export roadmap --template my.md.j2   # render once with another template; writes nothing
```

**Enforcement.** The pre-commit check behind `[enforce].generated_views` (`block` | `warn` |
`off`) covers exported documents as well as the rendered views: a staged file whose FIRST line
is a `ddflow:generated doc=<kind>` header of a known kind must equal a fresh export (body and
kind compared, not the ddflow version), the event log must be fully staged beside it, and a
stale or hand-edited document is named with `ddflow export <kind> --update` as the remedy. A
hand-written file that only holds a marker region has no such first line, so it is not treated
as a generated file; an append-mode log (`last=` in its header) grows by design and is skipped.
Every SELECTED whole or append export target is also excluded from `[enforce].stale_docs`
(`shared_files.doc_exclude`) and from docscheck (generated documents are judged by `export
--check`; a region target's hand-written text is still scanned), every selected target is a shared path (no claim needed), and draws no "no merge strategy" note from
`doctor`; `doctor` instead reports a selected target that is stale or hand-edited as a NOTE.

Exit codes: 0 done or fresh, 1 stale (`--check`), 2 could not run (unreadable log, template
error, nothing selected) or declined at the prompt, 3 refused. A failure is never an empty
clean document.

**MCP.** `ddflow_export` with no `doc` lists the kinds; with a `doc` it returns the
markdown and says `truncated: true` (with `truncated_more`) when it was cut, bounded at 60,000
bytes whatever `max_bytes` asks. It writes only with `write=true` AND a repo-relative `path`
(`path` alone is refused); `diff` and `check` compare against a path. It has no `force`, no
`update` and no ad hoc template: an agent never overrides hand-edit protection or points the
renderer at an arbitrary file. `action` = `list`, `enable`, `disable` (with `doc`, and `path` /
`mode` for enable) or `validate` does the matching selection or template check; an enable over
MCP is always an agent's (it names the agent and the stop command), and MCP cannot lock,
acknowledge, eject or edit a template. It is in the `all` tool tier only.

**The `[export]` knobs** (5 of the 675): `documents` (the selection, default `[]`), `redact`
(default `true`), `max_bytes` (the stdout / MCP cap, default 60000; a written file is never
capped), `refresh` (`off` | `merge` | `phase_close` | `docs_gate`, default `off`) and `tables`
(the per-document tables below). Each document may have a table:

```toml
[export]
documents = ["roadmap", "status"]

[export.roadmap]
path = "docs/ROADMAP.md"      # default: ROADMAP.md at the repo root
mode = "whole"                # whole | region | append
template = "tools/roadmap.md.j2"
filters = { limit = 50 }
```

Unknown keys are skipped with a warning (a newer release's config does not stop an older
checkout); `ddflow config --set` refuses them. `ddflow help export` has the same material.

**Automatic refresh.** `refresh` says when a SELECTED whole-file document regenerates itself;
set it for all documents in `[export]` or for one in `[export.<doc>]` (the per-document value
wins, so `refresh = "off"` there opts one document out):

| mode | when |
| --- | --- |
| `off` (default) | never: nothing writes unless you run `ddflow export --update` |
| `merge` | `ddflow merge` regenerates the document into the item's branch and commits it there, so it lands in the merge commit (as of just before the landing) |
| `phase_close` | completing a phase regenerates it in the working tree (not committed) |
| `docs_gate` | the phase `docs` gate's export step: recording that gate `passed` on a phase regenerates the documents, verifies them (`export --check`) and stores them with their body digests in the gate's evidence (`evidence.export`) |

Every mode keeps the same guards: a hand-edited or unmarked file is never touched (the result
says `skipped`), a region or append document is not refreshed automatically, a refresh that
fails (unreadable log, template error) is reported and never fails the merge or the
completion, and with nothing selected for that trigger it does nothing and says so. The
`ddflow merge` and `ddflow complete` output carries an `export_refresh` result (`--json`:
documents, actions, digests) and prints a one-line summary. `ddflow cadence` lists
`export_refresh` as due when an opted-in document is stale.

---

## Customisation reference

One place to look up what can be changed. "Scope" says where a change lives: **shared**
is committed and every clone sees it; **local** is `.ddflow/local/`, git-ignored, this
machine only. Precedence for settings is default → shared file → local file →
`DDFLOW_<SECTION>_<KNOB>` environment variable. Run `ddflow config --explain` for every
knob with its value, source and documentation.

### Files you can edit

| File | Scope | What it controls | Change it with |
|---|---|---|---|
| `.ddflow/config.toml` | shared | Every knob (sections below), `[gate.*]`, `[[reviewer]]`, `[[macro]]` | editor, `ddflow config --set/--append-toml`, `ddflow_configure` |
| `.ddflow/gates.toml` | shared | Gate definitions and human-approval checkpoints | editor, `ddflow workflow gate` |
| `.ddflow/companions.toml` | shared | Companion tools the gates expect (what each buys, how to install) | editor |
| `.ddflow/macros.toml` | shared | `[[macro]]` modes, if split out of `config.toml` | editor |
| `.ddflow/local/config.toml` | local | Machine-specific knobs, worker counts, `enforce.*` overrides | `ddflow config --local --set` |
| `.ddflow/local/gates.toml` | local | Gates for this machine only | editor |
| `.ddflow/local/reviewers.toml` | local | Reviewer endpoints, model names, API-key variable *names* | `ddflow reviewers detect --write`, editor |
| `.ddflow/prompts/<name>.md` | shared | Override of a shipped prompt template (Jinja2) | `ddflow prompts eject <name>` |
| `.ddflow/templates/export/<kind>.md.j2` | shared | Format of each generated document | `ddflow export eject <kind>` |
| `.ddflow/rules/<id>.toml` | shared | Project rules agents are told to follow: TOML frontmatter, a blank line, the text. Besides `title`, `tags`, `scope`, `priority` and `globs`, a file may carry `categories`, `gates`, `category`, `enforcement`, `status`, `owner`, `review_by`, `sources`, `checks` and `links` (the schema rules share with decisions) | `ddflow rule add/edit/remove`, `ddflow_rule_*`, editor |
| `AGENTS.md` / `CLAUDE.md` | shared | The managed ddflow block plus your own prose | `ddflow adopt` (re-run), editor outside the block |
| `docs/ddflow/drivers/implement-phase.md` and `deltas/<agent>.md` | shared | The implementation driver and per-agent notes | edit (outside the stamped region freely; an edit inside it is kept in `<file>.local-edits` when `ddflow adopt --refresh-docs` resets it to shipped) |
| `.claude/commands/implement.md` (and other agents' command dirs) | shared | The slash command that drives the queue | edit after `ddflow adopt` |
| Agent MCP config (`.mcp.json`, `.cursor/mcp.json`, `.codex/config.toml`, …) | shared | How the agent launches ddflow; see [Wiring it into your agent](adoption.md#wiring-it-into-your-agent) | `ddflow adopt --agents …` |
| `.ddflow/events/` | shared | The source-of-truth log. **Append-only: do not edit.** | the CLI/MCP only |

### Configuration sections (`.ddflow/config.toml`)

| Section | Controls |
|---|---|
| `gates`, `[gate.<id>]` | Task, phase and promotion pipelines; required gates; evidence requirements; ordering enforcement; skip policy |
| `schedule` | Parallelism cap, readiness policy, cycle and unknown-dependency handling, bugs-first, shared resources, sibling repos |
| `lease` | Lease TTL and heartbeat behaviour |
| `worktree` | Where and how isolated worktrees are made and cleaned |
| `flow` | Branching model (trunk, gitflow), integration (merge or PR), forge, PR behaviour, tags and versions, release lines, environments and promotion |
| `enforce` | Commit hooks: lease required, item trailers, forbidden trailers, stale docs and rules, README-with-code, branch drift |
| `review`, `[[reviewer]]`, `agent` | Reviewer endpoints, review rounds, the cross-family rule, model-family table, agent identity |
| `prompts` | Inline prompt overrides (alternative to files under `.ddflow/prompts/`) |
| `rules` | Rule count and size limits, allowed tags and scopes |
| `lessons`, `memory`, `reinstruct`, `session`, `log` | Lesson and memory limits, periodic re-instruction, session provenance, log handling |
| `cadence`, `dedupe`, `bugs`, `ci` | Periodic passes, duplicate detection thresholds, bug handling, CI parity checks |
| `importer` | Which files an import treats as plans, lessons, decisions, research, journals |
| `export` | Which documents are generated, redaction, size limits, refresh |
| `companions`, `mcp`, `loops`, `upgrade` | Companion tools, which MCP tools are exposed, loop detection, upgrade behaviour |
| `rag` | The Embedder: the companion command, a local model directory, its timeout |
| `[[macro]]` | Your own named prompt modes, exposed as slash commands and `ddflow prompts` |

### Prompts and workflow commands

Each is overridden with `ddflow prompts eject <name>`, which copies the shipped text to
`.ddflow/prompts/<name>.md`. `ddflow prompts list` shows where each resolves from.

| Name | Used for |
|---|---|
| `mcp_instructions` | The instructions an MCP client receives on connect |
| `gate_instruction` | What an agent is told at each gate |
| `review_system`, `review_user` | The reviewer's system and user prompts |
| `session_brief_header` | The header of the session-start brief |
| `implement` | Drive the queue to completion |
| `onboard`, `import-existing-project` | Install into, and import history from, an existing project |
| `research-companions`, `install-companions` | Choose and install companion tools |
| `bug-hunt`, `code-deduplication`, `code-clean`, `all-tests` | Periodic quality passes |
| `perf_report` | Answer performance questions from the perf report |

### CLI commands that change the project's setup

| Command | Changes |
|---|---|
| `ddflow config --set K V` / `--append-toml T` / `--explain` | Any knob; `--local` writes the machine-local layer |
| `ddflow workflow` / `workflow pipeline` / `workflow gate` / `workflow drop` | Show or change the gate pipelines and individual gates |
| `ddflow prompts list|show|eject` | Prompt templates |
| `ddflow rule add|edit|list|search|show|remove` | Project rules |
| `ddflow flow show` / `flow choose <knob> <v>` | Branching, release and PR choices, recorded with a reason |
| `ddflow reviewers detect|list|test|add|approve` | Reviewer endpoints |
| `ddflow companions` / `companions add` | List companion tools; register installed ones in an agent's MCP config |
| `ddflow export eject|validate|enable|disable` | Generated-document templates and selection |
| `ddflow adopt [--agents …] [--refresh-docs]` | Agent wiring, driver docs, rules blocks |
| `ddflow hooks install|uninstall|status` | Git commit hooks |
| `ddflow decision add` | Architectural decisions that govern file globs |

### MCP tools that change the project's setup

All require your agreement in the instructions they carry; most offer `dry_run`.

| Tool | Equivalent |
|---|---|
| `ddflow_configure` | `ddflow config --set/--append-toml` |
| `ddflow_workflow`, `ddflow_workflow_pipeline`, `ddflow_workflow_gate`, `ddflow_workflow_drop` | `ddflow workflow …` |
| `ddflow_rule_add`, `_edit`, `_remove`, `_list`, `_search`, `_show` | `ddflow rule …` |
| `ddflow_flow_choose`, `ddflow_flow_show` | `ddflow flow …` |
| `ddflow_reviewers_detect`, `ddflow_reviewers_list` | `ddflow reviewers …` |
| `ddflow_companions`, `ddflow_companions_add`, `ddflow_companions_verify` | `ddflow companions …` |
| `ddflow_prompts`, `ddflow_export`, `ddflow_hooks` | `ddflow prompts / export / hooks` |
| `ddflow_upgrade` | `ddflow upgrade` (the plan; reads only) |
| `ddflow_setup` | `ddflow adopt` |
| `ddflow_decision_add`, `_supersede`, `_list`, `_show`, `_applicable` | `ddflow decision …` |

Three things have **no MCP equivalent on purpose**, because an agent must not clear its
own checkpoints: `ddflow approve` (human gates), `ddflow reviewers approve`, and setting
`gate.<id>.human`. No writer can drop a human gate from a pipeline either.

### Environment variables

| Variable | Effect |
|---|---|
| `DDFLOW_<SECTION>_<KNOB>` | Overrides any knob (list and map values as JSON) |
| `DDFLOW_AGENT` | Who is calling, for attribution |
| `DDFLOW_REPO` | Which repository to operate on |
| `DDFLOW_MCP_TOOLS` | Which MCP tools are exposed |

---
