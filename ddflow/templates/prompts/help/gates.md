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
