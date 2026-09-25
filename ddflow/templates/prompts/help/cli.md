# Driving it from a terminal

Nothing here needs an agent. The CLI is the whole product; the MCP server is a second
surface over the same commands, and a test fails if the two diverge.

## A whole task, start to finish

```console
$ ddflow init                                  # creates .ddflow/
$ ddflow config --set gate.unit_tests.command "python -m pytest -q"
$ ddflow phase add P1 --title "Billing" --globs "src/billing/**"
$ ddflow task add P1.T1 --phase P1 --title "Tax rules" --globs "src/billing/tax.py"

$ ddflow next                                  # exit 2 = nothing actionable
Ready (1 ready, 0 running, 0 blocked):
  P1.T1  Tax rules

$ ddflow claim P1.T1                           # exit 3 = refused, with the reason
leased P1.T1 · worktree .ddflow-worktrees/P1.T1 · branch ddflow/P1.T1

$ cd .ddflow-worktrees/P1.T1
   ... edit, commit ...

$ ddflow gate status P1.T1                     # what the pipeline wants next
$ ddflow gate run P1.T1 unit_tests             # runs it; the exit code is the evidence
$ ddflow gate record P1.T1 implement --outcome passed --evidence "added tax.py"

$ ddflow complete P1.T1                        # exit 3 lists whatever is unsatisfied
$ ddflow merge P1.T1                           # from the primary checkout
```

## In a Makefile or CI

Exit codes are the interface, and they mean the same thing on every command:

    0  it worked            2  nothing to do, or could not tell
    1  it failed            3  refused

`2` is never "no problem". A job that treats it as success is a job that reports a
green build for a test suite that never ran.

```make
check:
	ddflow doctor                 # 1 = integrity problems, each named
	ddflow workflow               # 1 = the pipeline does not hang together
	ddflow cadence                # 2 = no periodic pass is due
```

Add `--json` to any command for the machine-readable form. `ddflow --json next` gives
the ready set with the reason each blocked item is blocked.

## What you get without an agent

Everything except the judgement. Command gates run; agent gates wait for someone to
record an outcome, and `ddflow gate record <id> <gate> --outcome passed --evidence
"..."` is how a human does that. `ddflow gate skip <id> <gate> --reason "..."` is the
documented escape hatch, and the reason is mandatory — a skip is recorded as a skip, not
as a pass.

See also: `ddflow help mcp` for the agent-driven path, `ddflow help customise` to
change the pipeline.
