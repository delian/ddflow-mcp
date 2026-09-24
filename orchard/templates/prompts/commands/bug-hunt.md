Hunt bugs{% if scope %} in {{ scope }}{% else %} across the work currently in flight{% endif %}, and fix what you find — under one hard rule.

## The rule

**A finding may not change source unless a runnable probe demonstrates it, and that probe ships as the regression test in the same commit.** Then revert the fix and watch the probe fail, then restore it. A probe that passes both ways proves nothing, and is the commonest way this gate gets satisfied on paper.

If you cannot write a probe, the finding is `THEORETICAL`: record it and change nothing.

    orchard_bug_fixed   — refuses to close a bug without naming its regression test

## Scope it first

An unbounded hunt over a whole repository samples arbitrarily and reports the sample as coverage. Bound it:

- `orchard_show` on the item(s) in flight, to see what actually changed;
- the callers of every symbol the change touched;
- the two or three bug classes plausible *here*, not the generic list.

## Classes worth hunting, in rough order of how often they are real

- **empty-collection / vacuous truth** — `all(...)` over an empty sequence is `True`; a check with no inputs passes
- **off-by-one** at boundaries, and the `<` / `<=` at the end of a range
- **silently dropped configuration** — a knob accepted and never read, a section skipped on a typo
- **unavailable treated as success** — a tool that could not run reported as one that found nothing
- **torn-tail resume** — half-written state on restart
- **concurrent-collective in control flow** — one process taking a branch the others do not
- **stale cache / stale bytecode** — the post-fix run re-executing the pre-fix code

## Report each finding as exactly one of

    CONFIRMED (probe: <test id>)   — the probe exists, ran, and was mutation-verified
    THEORETICAL (filed: <where>)   — no probe was possible; say WHY

A `CONFIRMED` with no `probe:` is not confirmed.

## Then

1. `orchard_gate_record <item> bug_hunt --outcome passed --evidence "<n found, n fixed, probes: ...>"` — or `unavailable` with a reason if you could not hunt at all. Never a bare pass.
2. `orchard_lesson_add` for anything whose *class* could recur — the rule, not the instance.
3. `orchard_loops` if the same bug keeps coming back; work that will not stay fixed is a loop, and re-fixing it is not the remedy.
