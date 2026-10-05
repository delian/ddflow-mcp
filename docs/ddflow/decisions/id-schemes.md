# D-id-schemes — configurable id schemes and vocabulary

Status: accepted · decided by the operator, 2026-10-05 (B-id-decide, phase P-id-schemes)

## Context

Every id ddflow mints today has a fixed shape:

- auto ids from `core/ids.auto_id`: a one-letter prefix and ten hex digits of a
  blake2b hash salted with a nanosecond clock (`B9c56de9d58`, `L…`, `R…`);
- session ids from `services/sessions.new_session_id`: `s<UTC timestamp>-<pid>`;
- CI failure bugs from `services/ci.bug_id`: `Bci-<slug>-<sha1[:10]>`;
- tasks and phases: whatever the caller names.

The operator asked (2026-10-05) for project-chosen naming for bugs, phases, tasks,
research, workflow and every other record, keeping today's shapes as the default. The
log is append-only and shared by several clones, so a scheme has to work offline and
must never rewrite a recorded id.

## Decision

1. **Templates per kind.** `[ids]` holds one template per record kind. The default
   template for each kind reproduces today's ids exactly; a project that sets nothing
   sees no change, and existing logs replay byte-identically.

2. **Tokens: the full set.** `{prefix}` `{seq}` `{date}` `{slug}` `{hash}`
   `{parent}` `{phase}` `{env}` `{user-text}`. Templates are validated when the config
   is written: every token known, the result a valid id (characters, length), and a
   template that could produce the same id twice without `{seq}` or `{hash}` refused.

3. **Sequential numbers: next free number, suffix on clash.** `{seq}` is the highest
   number already used for that kind in the folded log, plus one, taken under the log
   lock. All agents on one machine share the log, so numbers are unique there. Two
   clones minting offline can pick the same number; when their logs meet, the record
   that is later in the fold order keeps its id unchanged and gains the alias
   `<id>.<clone suffix>` (for example `BUG-123.b`), which doctor and `show` explain.
   Nothing is renumbered.

4. **Old ids: aliases only.** A recorded id is never renamed. A scheme change applies
   to records created afterwards. Any record, old or new, may carry aliases (project
   labels), accepted everywhere an id is accepted (B-id-aliases). There is no bulk
   migration that aliases every old record.

5. **Vocabulary reaches the names people type.** `[vocabulary]` maps the kinds to the
   project's words (task/story, bug/defect, phase/epic, research/spike, singular and
   plural). Those words are used in CLI output, MCP reply text, prompts, the brief and
   exported documents, AND as aliases for commands and MCP tools: `ddflow story add`
   is `ddflow task add`, and an MCP call to the project-worded tool name is accepted.
   The canonical command and tool names keep working. Event kinds, JSON keys and the
   canonical MCP tool names never change, so the log, tooling, replay and other
   clones are unaffected. Aliased MCP tools are only advertised in `tools/list` when
   the tool tier allows (the list has a size budget). A call by alias is accepted
   whether or not it is advertised.

## Consequences

- One id service (B-id-generator) mints every id, so no call site formats ids itself.
- `{seq}` needs the folded state at mint time, which is cheap because the fold is
  cached. An offline clash produces a visible suffix, not a silent duplicate.
- The vocabulary work (B-id-terms) also covers command and tool aliases. The parity
  test must treat an alias as the canonical command.

## Alternatives rejected

- Per-clone number blocks: numbers jump and are not chronological.
- Numbering at merge time: the number is unknown while the work is in flight.
- Hash ids only: this does not give projects the JIRA-style keys they asked for.
- Bulk alias migration: it adds an alias to every old record for appearance only.
- Display-only vocabulary: the operator wants to type their own words.
