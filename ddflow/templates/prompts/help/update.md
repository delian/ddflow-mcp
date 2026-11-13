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

## Correcting a recorded value

`ddflow update` edits a record; `ddflow correct` is for a value that was recorded WRONG (a typo, a
wrong fact, a wrong author model on a completion, a leaked value) and says why, with history kept:

    ddflow correct B1 --field summary --text "the right text" --reason "wrong fact"
    ddflow correct T1 --field author_model --text "<the true model>" --reason "recorded the wrong one"
    ddflow correct L1 --field rule --from-file new.txt --reason "pasted the wrong paragraph" --secret

Over MCP it is the `correct` action of `ddflow_task` (`id`, `field`, `text`, `reason`, `kind`,
`secret`, `approval`): one declaration serves both. It writes a `record.corrected` event; the
record shows the new text, and `show` and `history` list the correction (who, when, why, which
fields). The reason says why the value was wrong, never what it was.

**The old text is still in the event log.** A correction replaces what the record shows; it
removes nothing, because the log is append-only and committed. If the old value was a secret,
a physical scrub of the log removes it (a person's act, never an agent's) and the secret itself
must be rotated at its source. The audit entry keeps a digest of the old or new value only when `[records]
correct_digest_old` / `correct_digest_new` are on (off by default: a digest of a low-entropy value
lets a guess be confirmed), never when `--secret` says the value may be one, and never for a value
shorter than `[records] correct_min_digest_chars`. The new text goes through the same write-time
redaction as every write.

Who may: a person corrects anything. An agent corrects a record it authored and a record on an
item it holds a lease on, without asking (audited); anything else needs the operator:
`--approval request`, the person answers in chat, then re-run with the approval id.
