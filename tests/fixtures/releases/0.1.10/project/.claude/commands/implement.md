---
description: Drive the ddflow queue to completion, unattended — every gate, merged and completed, via /loop
argument-hint: "[phase or task id] [guidance]"
---
<!-- DDFLOW:MANAGED — `ddflow adopt` rewrites this file; delete this line to keep your own -->

Run `/loop` with no interval (self-paced) on this prompt, verbatim:

> Drive this project's ddflow queue with the ddflow `implement` workflow command. Fetch it
> with the `ddflow_prompts` tool (`action: show`, `name: implement`) — or, from a shell,
> `ddflow prompts show implement` — and follow it for this iteration. Its scope is:
> `$ARGUMENTS`. Where the template reads `{{ scope }}`, substitute that scope; where it
> branches on whether a scope was given, an empty scope means the whole queue. Harness
> mechanics — arming the continuation, subagents, asking the operator — are in
> `docs/ddflow/drivers/deltas/claude-code.md`.

The same command is also this server's MCP prompt, `/mcp__ddflow__implement`, for a single
supervised pass without `/loop`.
