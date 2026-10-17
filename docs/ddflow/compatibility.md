# Compatibility contract

Status: written for ddflow 0.1.18 · decision D-compat (operator, 2026-10-06) · research
R-compat. This page says what ddflow promises not to break, how every change is classified,
and how a release is numbered. `tests/test_compat_contract.py` keeps the parts that code can
check in step with this page.

## What is an interface

Anything a person, an agent, a script or another ddflow can depend on without reading the
source. Each kind below is an interface; everything else (private functions, module layout,
the SQLite index, wording of human-readable text) may change in any release.

| Interface | What exactly |
|---|---|
| CLI commands and flags | every command and sub-command, every flag and its value forms, exit codes (`0` ok, `1` failure, `2` could not run or nothing to do, `3` refused) |
| MCP tools | tool names, argument names and types, result fields |
| `--json` output | every field of every `--json` result |
| Config keys and values | every `[section].knob`, its type, and for an enum knob its allowed values |
| Event kinds and payloads | every `kind` in `.ddflow/events/*.jsonl` and every field of its `data` |
| Export template data | the data a document template receives, versioned by `schema_version` |
| Prompt template variables | the variables a shipped or ejected prompt template may use |
| Hook commands | every command line ddflow writes into a project (git hooks, Claude Code hooks, MCP server entries) |
| On-disk formats | the event log, the machine-local stores under `.ddflow/local/`, and generated files with a `ddflow:generated` header |

## How a change is classified

Every change to an interface is exactly one of these three.

**Additive.** Allowed in any release. Examples: a new command, flag, tool, argument with a
default, result field, config key with a default, enum value that is never a default, event
kind, or event field that readers may ignore. An older ddflow ignores what is new, and keeps
it intact when it rewrites the file that holds it (below).

**Deprecating.** Allowed in any release. A command, flag, MCP tool, tool argument or config
key is renamed or replaced, and the old name keeps working through a declared alias that
prints a one-line deprecation notice. The alias is hidden from `--help` and `tools/list` and
is never removed before ddflow 1.0 (D-compat 1). `ddflow upgrade` rewrites the references
ddflow itself manages (hooks, MCP entries, its own config writes) to the new name.

**Breaking.** Anything else: a removal without an alias, a changed meaning or type, a
narrowed set of accepted values, a field that becomes required, an event field that is
removed or retyped. A breaking change needs a declared migration (an upgrade repair that
`ddflow upgrade --apply` runs and verifies) and an entry in the release's upgrade manifest,
and it is a minor-version bump while ddflow is 0.x.

## Version numbers

A release is numbered by the impact of its changes, not bumped by patch automatically:

- only fixes, additive and deprecating changes (nothing stops working): patch (`0.1.18` ->
  `0.1.19`);
- any breaking change, while 0.x: minor (`0.1.x` -> `0.2.0`);
- 1.0 is the release that drops the deprecation aliases.

`__version__` (in `ddflow/__init__.py`) says which release this is. It is not what decides
whether two ddflow versions can share a project; `FORMAT_LEVEL` is.

## FORMAT_LEVEL

`FORMAT_LEVEL`, an integer in `ddflow/__init__.py` (currently **1**), is raised by any change
that an older ddflow cannot read or write safely: a new on-disk format, an event payload
that needs an upcaster, a config section rewritten in a new shape. Additive changes never
raise it. It is separate from `__version__` because many releases change nothing on disk,
and a version comparison would refuse an older clone over a release that only added a flag.

## Every version number

ddflow carries several independent version numbers. None of them is derived from another, and
each answers a different question; this table is the one place that says which an upgrade may
change and how the data behind it moves. `tests/test_event_upcasters.py` checks that every
constant below is named here with its current value (the release version, which changes every
release, is checked by name only).

| Number | Value | What it versions | An upgrade may change it | How existing data moves |
|---|---|---|---|---|
| `ddflow.__version__` | the release | the code a PROCESS imported; stamped into the log by `ddflow.seen`; the skew guard compares it with the log's highest stamp, so a long-lived MCP server that outlives a self-upgrade keeps the old number, refuses writes once newer code has stamped the log and says to restart (`tests/test_upgrade_running_server.py`); `ddflow upgrade --plan` compares the project's stamp with it | every release | `ddflow upgrade --plan` / `--apply` bring the project to it (config, migrations, repairs, instruction files, hooks) |
| `ddflow.FORMAT_LEVEL` | 1 | the on-disk shape an older ddflow cannot read or write safely | only for a breaking on-disk change | a registered migration (`ddflow upgrade --apply migrations`) rewrites what it must and `verify`s; an older ddflow is refused a conflicting write |
| `config.CONFIG_FORMAT` | 1 | the optional top-level `format = N` of `.ddflow/config.toml` | only when a section's SHAPE changes in a way an older ddflow could not edit without losing something; an added knob never does | a file with a higher `format` is read (what this version knows applies) and not written (exit 3, upgrade advice); a file with none is format 1; `ddflow upgrade --apply config` rewrites by the manifest |
| `core.events.SCHEMA_VERSION` | 1 | the envelope of every event (id, kind, subject, agent, lamport, ts, schema) | only with a `FORMAT_LEVEL` bump | none: the log is never rewritten. A reader meeting a higher `schema` counts the event as skipped and says to upgrade; it never guesses |
| payload versions (`core.upcasters.PAYLOAD_VERSIONS`) | none above 1 yet | one event kind's `data` shape, per kind (`data["v"]`, absent = 1) | a kind at a time, with an upcaster for each step and an entry on this page | the fold upcasts when it reads, so old events keep folding unchanged on disk; an older reader skips a newer payload and counts it |
| `infra.log.SNAPSHOT_FORMAT` | 1 | the local read snapshot of the parsed log (`read-snapshot.bin`, machine-local, never committed) | any release that changes how events are stored in it | none to run: a snapshot whose format, ddflow version, field layout or parser differs is ignored and the log is parsed again; one snapshot is kept per fingerprint and stale ones are collected |
| `infra.store.SCHEMA` | 11 | the derived SQLite index (search, recall) | any release that changes its tables | none to run: the index is disposable, and a different SCHEMA, similarity version or code fingerprint rebuilds it from the log on the next read |
| `core.textsim.VERSION` | 2 | the similarity tokens, weights and record digest | any release that changes how text is compared | the index stores it and rebuilds when it differs; nothing outside the index keeps it |
| `services.ticks.FORMAT` | 1 | `.ddflow/local/ticks.json`, machine-local schedule state | rarely | written beside the data but not checked by readers yet: a shape change must stay readable by the old reader or be recomputed (an unreadable file reads as empty) |
| `services.quota.STORE_VERSION` | 1 | the user-level `quotas.json` (usage quotas) | when the file shape changes | a newer file is read for the fields this version knows and kept whole when this one writes; an older or missing version is refused |

The local state files that carry NO version are the markers and caches under `.ddflow/local/`
(`upgrade-notice.json`, `upgrade-start.json`, `release-check.json`, `companion-probes.json`, the
wait records) and the user-level `self-upgrade.json`. They are rebuilt or ignored when
unreadable, so a change to one has to stay readable by the previous release or be harmless to
lose; B-arch-local-state adds a format key to them. Nothing in this table is stored in the
project's committed files except the event log (`schema`, `v`) and the `ddflow.seen` stamps
(`version`, `format_level`).

Additive payload keys change none of the numbers above. `decision.recorded` gained `data["saw"]`
(B0dcccd8594): the ids of the live decisions whose globs overlapped the new one when its author
wrote it. An older reader ignores the key and keeps it when it rewrites the file; no event kind
and no required key is new, and `infra.store.SCHEMA` stays 10 because the index does not store
it. A decision without the key (every log written before) never forms an unreconciled pair, so
an old log folds exactly as before. The fold marks two live decisions on overlapping globs that
do not name each other as unreconciled; `doctor`, `brief` and `decision applicable` show it.

## An older ddflow meeting newer data

Several clones of a project run different ddflow versions. An older ddflow that meets data
a newer one wrote (D-compat 2):

- reads and writes what it understands;
- preserves everything it does not understand, untouched: unknown config keys and sections,
  unknown event kinds and fields, unknown regions of managed files;
- is refused (exit 3, "upgrade ddflow to >= X") only on a DIRECT CONFLICT: a write that would
  lose, overwrite or downgrade content a newer version wrote -- re-rendering a managed file
  stamped by a newer ddflow, rewriting a config section in a newer format, minting ids under
  a scheme it cannot apply.

A refused write is never silent and never partial; a read never fails because something is
newer than the reader.

### Patch skew

When the log's highest stamp is ahead of the running ddflow by PATCH releases only -- the same
major and minor, the same `FORMAT_LEVEL`, and no recorded capability the running ddflow lacks for the kind written
-- the write is NOT refused (B63635ddc6a, D-mu-bugfix-answers): it goes through with a
one-time warning on stderr, and each event it writes carries the `older_ddflow` mark. A higher
`FORMAT_LEVEL`, a higher minor or major, or a capability gap on the kind written still refuses (exit 3);
`[upgrade].skew = "off"` is the operator's. `--allow-older-version` has nothing to override
under a patch skew. The relaxation covers LOG writes only: commands that rewrite tracked files
without appending an event (`adopt`, `adopt --refresh-docs`) ask the guard first and are refused
by ANY older stamp, a patch behind included (the managed text is worded per release), so an
older ddflow does not re-render what a newer one wrote (under the default `refuse` policy; `warn`
and `off`, and a session override, are the operator's). `doctor` reports a file holding more than one managed block (both marker
grammars) as a PROBLEM, and `adopt --refresh-docs` collapses them to one, keeping every byte
outside the markers; a block stamped at a newer format level is never rewritten. A marker is a
whole line starting in column 0: a pair shown that way (even inside a fenced example) counts as
a block, while one indented or quoted inside a sentence does not; the refresh backs the original
up first (`.ddflow/backups/`).

## Enforcement

What checks this contract, and which tasks complete it:

| Check | Where |
|---|---|
| this page, `FORMAT_LEVEL` and the README link stay in step | `tests/test_compat_contract.py` |
| deprecated names keep working as hidden aliases | B-uni-compat-aliases |
| unknown config, events and regions are preserved; only direct conflicts are refused | B-uni-compat-config, B-uni-compat-events, B-uni-compat-artifacts |
| every `--json` and MCP result names its schema version | B-uni-compat-json |
| surface snapshots (help tree, `tools/list`, JSON schemas, `config --explain`, event kinds) compared with the previous release | B-uni-compat-tests |
| the release's bump level comes from the declared impact in the upgrade manifest (`scripts/release_impact.py`, read by `publish.yml`), and an undeclared surface change or a too-small version step refuses the release (`scripts/release.sh`) | B-uni-compat-release-gate: `tests/test_release_gate.py` |
