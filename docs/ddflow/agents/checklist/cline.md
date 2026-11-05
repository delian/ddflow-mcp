# Cline: tier-2 checklist

Cline has no headless run, so a person walks this list instead of a live smoke
(D-agent-compat). Set the two lines below when you finish; `ddflow harness record`
flags a date older than `[hooks].fixture_max_age_months`. Until then the agent is unproven here.

Agent version: unverified
Date walked: never

Descriptor: `ddflow/harnesses/cline.toml`. Cline's MCP settings are one global file, so
`ddflow adopt` has no project file to write for it.

## Adopt

`ddflow adopt --agents cline` in a throwaway git repository. It writes rules only. Check it
with `ddflow harness doctor cline`.

## Approve the MCP server

Add a server named `ddflow` to Cline's global MCP settings by hand: its command is `ddflow mcp`,
run in the throwaway repository. Approve it in Cline. That file is yours: remove the entry after.

## Prompt capture

Cline's hooks use the `cline` style (`.cline/hooks/`, `prompt_submit`), which ddflow has no
writer for, so nothing captures a prompt by hook. Pass only if the first MCP call records it;
note which at the end of this file. `harness record` cannot tap this agent.

## Round trip

1. `ddflow phase add P1 --title 'checklist'`, then `ddflow task add T1 --phase P1 --title 'add one file'`.
2. Ask the agent: call `ddflow_brief`, claim T1, add one file, complete it or release it.
3. Pass when `ddflow show T1` and `ddflow history` show a claim under an identity of its own, the
   task completed or released, and no lease left behind.
4. Fail, with the step, in a bug: `ddflow bug found --summary "..."`.
