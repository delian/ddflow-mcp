# Verifying a completion

A `done` mark is a claim: "this landed, it was tested, its gates held". `ddflow verify`
re-derives that claim from the log and git, so a false positive (marked done, not done) or a
false negative (done, but still open) is found by a command, not by luck.

## One task

    ddflow verify <id>

It checks eight things and says which hold (`ok`), which are worth a look (`WARN`), which do
not hold (`FAIL`) and which it cannot tell (`??`):

    landed          the recorded commit exists and is on the integration branch
    declared_files  a file the task declared exists (an exact path that never existed fails;
                    one that existed once, or exists only untracked, is a warning)
    tests           the tests it added still exist; code with no test is a warning
    gates           every pipeline gate has an outcome, required gates PASSED, a skip has a
                    reason (what `complete` itself enforces, asked again of the record)
    survives        what the landing changed was not removed afterwards
    regression      a fix task's bug is closed with a regression test that exists
    requirement     whether its requirement text was edited after completion
    ledger          whether the evidence is the completing agent's own or was reconstructed

Exit 1 when a claim does not hold, 2 when the task is not done, 0 otherwise. "Cannot tell"
is never reported as ok.

## The ledger and old completions

`complete` writes a ledger onto the completion: the requirement digest, the files and tests
the landing changed, skipped gates, the forced flag. `ddflow show <id>` prints it. A
completion from before ledgers existed is rebuilt: from the merge sha the log recorded, or
a commit whose subject is `<ID>: ...` / `merge <ID>: ...`. That is reconstructed evidence
and says so; it is never written into the log. A task imported as already closed has no
gate or landing history and is "cannot tell", not failed.

## Every task, and what to do about it

    ddflow verify --all [--limit N]       worst first, bounded
    ddflow verify --phase <id>
    ddflow verify --all --file-bugs       a bug (and fix task) per completion that fails

The sweep over a project that has history is mostly "cannot tell"; read the ones that FAIL.

    ddflow verify <id> --reopen           send a failing completion back to the queue

The task returns to `open` with its gates cleared (the log keeps what they were) and its
brief starts with the failed claims. `--reopen` on a completion that holds is refused unless
`--force --reason "..."`.

For the other direction, `ddflow verify <id>` on a task that is NOT done says when its work
appears to have landed anyway, and how to record it done without redoing it:
`ddflow complete <id> --sha <sha> --force` (the override is recorded).

## An independent second opinion

    ddflow verify <id> --pack             the evidence a verifier needs, bounded
    ddflow verify <id> --judge            have the cross-family reviewer judge it

The pack holds the requirement as it stood at completion, what landed, the gate history and
the mechanical findings; everything written by a person or agent is fenced as data. `--judge`
runs the optional `verify` gate: a finding is a requirement clause the evidence does not show
as met. No reviewer configured is recorded `unavailable`, never as a pass. The `verify` gate
is not in the default pipeline.
