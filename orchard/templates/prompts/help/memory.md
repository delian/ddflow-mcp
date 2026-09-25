# What the project remembers

One question, one command:

    orchard recall "<what you are about to do>"

It searches decisions, lessons, research, bugs, tasks and past operator prompts at once
and ranks them together — because "have we been here before?" is one question, and
making you run five searches is how it goes unasked.

What it draws on, and what each is FOR:

- **Decisions** (`orchard decision add`) — a choice between real alternatives, with the
  reason. Scoped to globs, so the ones governing the files you are about to edit surface
  when you touch them. A decision is binding unless the operator says otherwise.
- **Lessons** (`orchard lesson add`) — what surprised you and what to do instead. Advice,
  not law.
- **Research** (`orchard research`) — a finding with a verdict: CONFIRMED, REFUTED or
  THEORETICAL. A REFUTED entry is worth as much as an adopted one; it is what stops the
  next session re-researching something a probe already killed. CONFIRMED and REFUTED
  require a probe.
- **Bugs** (`orchard bug found` / `orchard bug fixed`) — and a bug cannot be closed
  without its regression test.
- **Sessions** (`orchard session start|prompt|note|end`) — what the operator actually
  asked for, in their words. This is what makes `orchard replay` able to reconstruct the
  project's intent rather than just its diff. Secrets are redacted on the way IN, because
  the log is committed.

`orchard brief` packs the relevant subset into a budgeted session-start pack, so the
cost of starting a session stays flat as the project grows.
