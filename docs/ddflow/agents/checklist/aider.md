# Aider: tier-2 checklist

Aider has no headless run, so a person walks this list instead of a live smoke
(D-agent-compat). Set the two lines below when you finish; `ddflow harness record`
flags a date older than `[hooks].fixture_max_age_months`. Until then the agent is unproven here.

Agent version: unverified
Date walked: never

Descriptor: `ddflow/harnesses/aider.toml`. Aider has no MCP client and no hooks, so it reaches
ddflow only by running the CLI: the MCP steps below are documented absences.

## Adopt

`ddflow adopt --agents aider` in a throwaway git repository. Aider loads a read-only context file
named by `read:` in `.aider.conf.yml`: check that it names the ddflow instructions.

## Approve the MCP server

None: Aider has no MCP client. Pass this step by confirming `ddflow harness show aider` says so.

## Prompt capture

None: Aider has no hook. The operator's words reach the log only through
`ddflow session prompt`. Pass by running that once and seeing it in `ddflow history`.

## Round trip

1. `ddflow phase add P1 --title 'checklist'`, then `ddflow task add T1 --phase P1 --title 'add one file'`.
2. Ask Aider to run `ddflow brief`, `ddflow claim T1`, add one file, then complete or release it.
3. Pass when `ddflow show T1` and `ddflow history` show a claim under an identity of its own, the
   task completed or released, and no lease left behind.
4. Fail, with the step, in a bug: `ddflow bug found --summary "..."`.
