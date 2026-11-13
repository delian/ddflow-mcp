# Gates

A gate is one checkpoint with one outcome. An item cannot complete until every gate in
its pipeline carries one, and `ddflow complete` names the ones that do not.

Two kinds, and the difference is the whole design:

- **Command gates** run something. `ddflow gate run <id> unit_tests` executes the
  configured command and records its exit code as the evidence. A command that *ran and
  failed* is `failed`; a command that *could not run* — binary missing, directory gone,
  timed out — is `unavailable`. Collapsing those two lets a tool that quietly stopped
  being installed read as a suite that quietly started passing.
- **Agent gates** are performed by you: a rubber-duck read, a standards check, a bug
  hunt. `ddflow gate record <id> <gate> --outcome passed --evidence "..."` — and the
  evidence is the point, because nothing else can check that you did it.

`ddflow gate status <id>` shows the pipeline, where the item is in it, and the
instruction for the next gate.

## The base branch's failures

ci and unit_tests read how the base fails from a per-project store, one entry per base
sha and gate command (`.ddflow/local/baseline-store/`, git-ignored). A sha not in it is
measured once under the suite lock and stored; a partial measurement is never treated as
complete; a failure that exists only for being in a worktree is not stored. Each failing
test id keeps a digest of its failure text. `[baseline].keep` days bounds it (0 keeps
nothing).

## Proving a gate can fail

A gate that cannot go red is worse than no gate: it reports success on every change and
everyone downstream reads that as evidence.

    ddflow gate verify <id> <gate>

It breaks what the gate guards — using `mutations` registered beside the gate — and
requires the gate to notice. A mutation that did not apply is a FAILURE, not a skip. A
gate with no registered mutations is reported as unproven. And the gate must PASS on
unmutated source first: one already-red gate reports `failed` for every mutation, so
every mutation would read as "detected" and prove nothing at all.

## Reviewers

The `critic` gate wants a reviewer from a different model family than the author's,
because a same-family reviewer shares the author's blind spots and its agreement is not
independent evidence. `ddflow reviewers detect` finds one; `ddflow review <id> --gate
critic` runs it and records the result. An unavailable reviewer is recorded as
unavailable — never as a pass.

A gate gets `[review].max_rounds` (default 2) review rounds per item, full and delta
alike; a third is refused. After that, `ddflow review triage` settles each remaining
finding (always allowed): refute it with the run that shows it false, or confirm it with
the test that now passes; the triage that settles the last finding records the gate passed on refutation (flagged in `gate status`), and one you cannot settle goes to the operator. An extra round past the cap is `ddflow review <id> --gate G --force --reason "..."`; an agent adds `--approval request` to file the operator approval and re-runs with the id it names (the route `gate skip` and `merge` have). Change the cap with `ddflow config review.max_rounds N` (0 =
unlimited; add `--local` for this machine) or `review.on_exceed = "warn"`.

Once a gate has a recorded review, `ddflow review <id> --gate G` is a full re-review: the
item's whole diff plus the previous findings and your triage of each, so the reviewer checks
each fix or probe and looks for new issues (decision D-gate-economy; counted against the
cap). `--delta` reviews only the commits since the head that review covered: the output
says "delta review of N commits since <sha>", the delta's findings merge into the gate's
record and earlier triage stays on the findings that are unchanged; a rebased branch falls
back to a full round and says why. `ddflow config review.delta_default true [--local]` makes
every plain re-review a delta (`--full` then still asks for a full round).

## Failure text across trees

A failure that prints a path prints the checkout it ran in. Before two failures are
compared by digest, the ci scratch tree, a base scratch tree and a lane worktree are
rewritten to `<tree>` and a pytest temp directory to `<pytest-tmp>` (`ddflow/services/ci/failure_text.py`), so the same
failure on the base and on the candidate has one digest and an old red is not read as new.
A different message or a different repo-relative path still differs.
