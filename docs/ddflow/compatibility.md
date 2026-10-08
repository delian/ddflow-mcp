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
