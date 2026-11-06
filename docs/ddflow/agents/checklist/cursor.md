# Cursor: tier-2 checklist

Cursor has no headless run, so a person walks this list instead of a live smoke
(D-agent-compat). Set the two lines below when you finish; `ddflow harness record`
flags a date older than `[hooks].fixture_max_age_months`. Until then the agent is unproven here.

Agent version: unverified
Date walked: never

Descriptor: `ddflow/harnesses/cursor.toml`, verified from documentation only.

## Adopt

`ddflow adopt --agents cursor` in a throwaway git repository. It writes `.cursor/mcp.json`.
Check it with `ddflow harness doctor cursor`.

## Approve the MCP server

Open the folder in the Cursor IDE and enable the `ddflow` server if it asks. Pass when the
agent can list `ddflow_brief`. The descriptor does not state this approval step: note what you saw.

## Prompt capture

Hooks live in `.cursor/hooks.json` (`beforeSubmitPrompt` is the prompt event). Send one prompt,
then run `ddflow history`. Pass when the prompt is there. To record payloads, use
`ddflow harness record cursor --prepare <new directory>` and open that directory instead.

## Round trip

1. `ddflow phase add P1 --title 'checklist'`, then `ddflow task add T1 --phase P1 --title 'add one file'`.
2. Ask the agent: call `ddflow_brief`, claim T1, add one file, complete it or release it.
3. Pass when `ddflow show T1` and `ddflow history` show a claim under an identity of its own, the
   task completed or released, and no lease left behind.
4. Fail, with the step, in a bug: `ddflow bug found --summary "..."`.
