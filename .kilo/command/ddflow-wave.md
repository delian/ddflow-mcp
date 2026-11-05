---
description: Orchestrate a ddflow scope (a phase id, bugs, or an item id) to empty as a BACKGROUND subtask — fan ready items to task subagents, stop only on a question or when the scope is empty
agent: ddflow-orchestrator
subtask: true
argument-hint: <phase-id | bugs | item-id>
---
<!-- ddflow:begin commands/ddflow-wave ddflow=0.2.10 fmt=1 sha=a45e2819619f head=6a165d0ca0f6 -->
You are the `ddflow-orchestrator` subagent, launched as a BACKGROUND subtask (this file is
`subtask: true`), so the wave loop runs off the operator's main chat — async, no `ctrl+b`,
no foreground session goal. YOUR scope is `$1` (a phase id, `bugs`, or an item id); drive
exactly that scope to empty. The wave: one read of the scope's `ddflow next --json`,
claim each ready item, then fan the claimed items to `task` subagents by `tier`, re-read
after each merge/park, waits via background processes and `ddflow wait`, the wave position
persisted in kilo memory, and a `question` only at operator gates (an empty scope is
completion, not a question). Files are the repo's own `.kilo/kilo.json` MCP registration
— no setup needed.
<!-- ddflow:end commands/ddflow-wave -->
