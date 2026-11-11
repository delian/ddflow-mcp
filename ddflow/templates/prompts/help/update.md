# Update: one edit path for every record kind

`ddflow update <id>` (CLI) and `ddflow_update` (MCP) change any record, not only a task: the id
finds the kind (`bug:B1` or `rule:r-x` names it when two kinds share an id). The same
operations work on both surfaces, and every one is event-sourced: an update appends an event and
the original record is never rewritten.

    ddflow update B1 --append-body "what we learned"   add text after the body; the original is kept
    ddflow update B1 --title T --body B                set the title / REPLACE the body
    ddflow update B1 --add-tags a,b --remove-tags c    fold onto the current list (--tags replaces)
    ddflow update L1 --set why="..." --set seen_in=a,b a kind's own fields, repeatable
    ddflow update <id> --parent P --priority 5 ...     the work-item fields (task and phase)

Over MCP the same: `title`, `body`, `append_body`, `tags`, `globs`, `needs`, `parent`,
`priority`, plus `set` as `["field=value", ...]`, which also carries `add_tags=a,b`,
`remove_globs=x` and the other list folds (no extra tool: the tools list is paid for in every
session).

## What each kind accepts

{{ update_kinds }}

`body` is the kind's main text (a bug's summary, a decision's decision, a lesson's rule, a
research note's claim, a memory's text, a rule's content, a schedule's prompt). A field a kind
lacks is refused naming the kind, the field and the nearest verb: `bug B1 has no needs field.
... Nearest verb: ddflow bug tag`. Kinds declared as data (`ddflow record <kind>`: goal,
principle, waiver ...) take the same operations through their declaration. `ddflow update`
changes nothing when the result equals the record (exit 2).
