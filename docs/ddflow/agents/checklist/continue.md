# Continue: tier-2 checklist

Continue has no headless run, so a person walks this list instead of a live smoke
(D-agent-compat). Set the two lines below when you finish; `ddflow harness record`
flags a date older than `[hooks].fixture_max_age_months`. Until then the agent is unproven here.

Agent version: unverified
Date walked: never

Continue has no descriptor yet: `ddflow adopt` and `ddflow harness show` do not know it, and
nothing below is verified. Write `ddflow/harnesses/continue.toml` first
(`docs/ddflow/adding-a-harness.md`); this list then gains its facts.

## Adopt

Not possible yet: no descriptor. `ddflow init` in a throwaway git repository gives the project
files.

## Approve the MCP server

Add a server named `ddflow` to Continue by hand: its command is `ddflow mcp`, run in that
repository. Approve it if Continue asks. Note where the setting went: the descriptor needs it.

## Prompt capture

Unknown: whether Continue has a prompt hook is not verified. Send one prompt, make one MCP call,
then run `ddflow history`, and record what was captured.

## Round trip

1. `ddflow phase add P1 --title 'checklist'`, then `ddflow task add T1 --phase P1 --title 'add one file'`.
2. Ask the agent: call `ddflow_brief`, claim T1, add one file, complete it or release it.
3. Pass when `ddflow show T1` and `ddflow history` show a claim under an identity of its own, the
   task completed or released, and no lease left behind.
4. Fail, with the step, in a bug: `ddflow bug found --summary "..."`.
