# Onboarding an existing project

From "the MCP server is attached" to a workflow ddflow manages, verified end to end. One
command, `ddflow onboard <stage>`, runs each stage; the MCP tool `ddflow_onboard` takes the
same stage, `apply` and `accept`. A stage that changes something PROPOSES by default and
prints its report; nothing is written until you pass `--apply`.

    ddflow onboard                 the standing drift report (same as `status`)
    ddflow onboard <stage>         one stage's report
    ddflow onboard <stage> --apply act on what the report marked safe
    ddflow onboard <stage> --apply --accept NAME
                                   act on that named item only (repeat the flag for more)

## The stages

    status      the standing drift report: every check below that can run without the test
                suite, and none of the writes. Re-run it any day.
    preflight   leftover worktrees, branches and stashes, each marked merged (safe to remove)
                or holding unique work (never removed for you). `--apply` removes the
                merged ones; `--accept NAME` removes exactly those.
    legacy      rulebook lines (CLAUDE.md, AGENTS.md, slash commands, handoff documents)
                that teach the old hand-kept workflow, with its replacement for each;
                `--apply` freezes the imported files so a later edit fails loudly.
    memory      the agent harness's own per-project memory, offered as candidates to record,
                deduplicated against what is remembered. `--apply --accept ID` records them.
    test-gate   the test runner detected from the project's files and a baseline measured in
                a detached clean tree: the command to give the `unit_tests` gate.
    verify      every probe, including the full suite: the registered MCP entry answers a
                real handshake, hooks are armed and refuse a forbidden trailer, brief and
                next answer, the freeze holds.
    harness     the agent harness's own setup: the registered servers are trusted where the
                harness asks for that, hooks are installed at the best level the harness
                supports, and the level is stated. `--apply` fails while a step is open;
                `--accept ID` names a harness (default: every one this project adopted).

Use `status`, `preflight`, `verify` in a git repository only: in a directory that is not one
they say `git init` first and write nothing.

## Exit codes

0 the stage's report is clean; 1 a check failed; 2 nothing to do (or the stage could not
run: never read it as 0); 3 refused, for instance an unknown stage or a name the report did
not mark safe.

## What a stage will not do

It never deletes work that exists only in git, never edits your rulebook without showing
the diff, and never records a check that could not run as a pass: it says `unavailable`.
The history itself is imported by `ddflow import` (`ddflow help import`).
