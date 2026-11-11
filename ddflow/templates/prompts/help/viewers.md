# Viewers: list and show for tasks, phases, bugs, research, sessions and the log

Read the project through these commands, not by opening `.ddflow/events/*.jsonl`: the log is
many shards in Lamport order and only ddflow folds it correctly. Every viewer is read-only,
prints one line per record (`show` prints the whole record) and takes `--json`.

    ddflow task list --phase P2 --state open     tasks (id, state, title, owner, date), newest change first
    ddflow phase list                            phases with done/total task counts
    ddflow bug list [--all] [--sort severity]    OPEN bugs; --all adds fixed and invalid ones
    ddflow research list --state refuted         research notes; the state is the verdict
    ddflow session list [--state open|ended]     sessions; `session show <id>` reads one
    ddflow history --tail 20 [--item X]          the engineering log, one line per event
    ddflow show <id>                             one whole record: task, phase, bug, research note
    ddflow search '<text>' [--kind K] [--exact]  across records, sessions, prompts, rules, the log
    ddflow list <kind>                           the same as `<kind> list`; kind also takes lesson

List filters: `--state`, `--phase`, `--tag`, `--owner` (the lease holder; not `--agent`, which
is who you are), `--since` (ISO date), `--limit` (default 50; 0 is the 1000 maximum) and
`--offset` (paging). A kind offers only the filters it has data for, so `bug list --tag x` is
an error, not "everything". A cut list says how many matched and the `--offset` of the next page.

Exit codes: 0 = at least one row was listed, 2 = nothing matched (the message names the filters), 3 a refused value such
as an unknown `--phase`.

Over MCP these are one tool, `ddflow_list`, with `kind` = `task | phase | bug | research |
session | search` and the same filters (`id` with `kind=session` shows one session;
`kind=search` takes `query`, `mode`, `sources`). It returns 25 rows unless `limit` says
otherwise. The log is `ddflow_history`; one record is `ddflow_show`.
