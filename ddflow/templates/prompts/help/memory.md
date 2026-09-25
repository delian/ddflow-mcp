# What the project remembers

One question, one command:

    ddflow recall "<what you are about to do>"

It searches decisions, lessons, research, bugs, tasks and past operator prompts at once
and ranks them together — because "have we been here before?" is one question, and
making you run five searches is how it goes unasked.

What it draws on, and what each is FOR:

- **Decisions** (`ddflow decision add`) — a choice between real alternatives, with the
  reason. Scoped to globs, so the ones governing the files you are about to edit surface
  when you touch them. A decision is binding unless the operator says otherwise.
- **Lessons** (`ddflow lesson add`) — what surprised you and what to do instead. Advice,
  not law.
- **Research** (`ddflow research`) — a finding with a verdict: CONFIRMED, REFUTED or
  THEORETICAL. A REFUTED entry is worth as much as an adopted one; it is what stops the
  next session re-researching something a probe already killed. CONFIRMED and REFUTED
  require a probe.
- **Bugs** (`ddflow bug found` / `ddflow bug fixed`) — and a bug cannot be closed
  without its regression test.
- **Sessions** (`ddflow session start|prompt|note|end`) — what the operator actually
  asked for, in their words. This is what makes `ddflow replay` able to reconstruct the
  project's intent rather than just its diff. Secrets are redacted on the way IN, because
  the log is committed.

`ddflow brief` packs the relevant subset into a budgeted session-start pack, so the
cost of starting a session stays flat as the project grows.
