---
description: Orchestrate a ddflow scope to empty under one goal (waves of ready items to subagents; stops only on a question) — pass a phase id, `bugs`, or a super plan (WIP) id
agent: ddflow-orchestrator
subtask: true
argument-hint: <phase-id | bugs | item-id>
---

Start a session goal with the objective "drive ddflow $1 to empty" and run the
ddflow-orchestrator instructions: wave = `ddflow next --json` ready set, fanned to
task subagents by `tier`, re-read `next` after each merge/park, waits via
background processes and `ddflow wait`, wave position persisted in kilo memory, and
a `question` only at operator gates. `goal_report` when `ddflow next` ends 2 with
nothing ready. Files are the repo's own `.kilo/kilo.json` MCP registration —
no setup needed.
