# Devin Desktop: tier-2 checklist

Devin Desktop has no headless run, so a person walks this list instead of a live smoke
(D-agent-compat). Set the two lines below when you finish; `ddflow harness record`
flags a date older than `[hooks].fixture_max_age_months`. Until then the agent is unproven here.

Agent version: unverified
Date walked: never

Descriptor: `ddflow/harnesses/devin.toml`. Cloud Devin is tier 3: its sessions are configured
in the web UI, which no repository file can do.

## Adopt

`ddflow adopt --agents devin` in a throwaway git repository. It writes `.devin/mcp_config.json`.
Check it with `ddflow harness doctor devin`.

## Approve the MCP server

Open the folder in Devin Desktop and approve the `ddflow` server if it asks. Pass when the
agent can list `ddflow_brief`. The descriptor does not state this approval step: note what you saw.

## Prompt capture

Hooks live in `.devin/hooks.v1.json` (`UserPromptSubmit`). Send one prompt, then run
`ddflow history`. Pass when the prompt is there. To record payloads, use
`ddflow harness record devin --prepare <new directory>` and open that directory instead.

## Round trip

1. `ddflow phase add P1 --title 'checklist'`, then `ddflow task add T1 --phase P1 --title 'add one file'`.
2. Ask the agent: call `ddflow_brief`, claim T1, add one file, complete it or release it.
3. Pass when `ddflow show T1` and `ddflow history` show a claim under an identity of its own, the
   task completed or released, and no lease left behind.
4. Fail, with the step, in a bug: `ddflow bug found --summary "..."`.
