The knowledge-dedupe cadence pass: find the records that were filed twice and settle
each one. The sweep is READ-ONLY; settling is a separate, recorded act.

## 1. List the pairs that are still open

    ddflow dupes --open-only

Each pair is two ids, their kinds and titles, and a score (0-1). The sweep skips pairs
already linked or dismissed, so anything it prints is unanswered. A score is a prompt to
**LOOK**, not a verdict: below the ask threshold the matcher cannot tell a duplicate from
a different-but-similar record (`R-dedupe-matchers`), and hard negatives sit inside the
duplicate band. Open both records before deciding.

Narrow it if the list is long:

    ddflow dupes --open-only --kind lesson
    ddflow dupes --open-only --limit 20

## 2. Settle each pair, one recorded link at a time

    ddflow link <a> --duplicate-of <b>     # a is the same thing as b
    ddflow link <a> --extends <b>          # a adds to b
    ddflow link <a> --related <b>          # related, but not the same
    ddflow link <a> --distinct <b>         # NOT a duplicate: dismiss the pair for good

`--duplicate-of` and `--extends` between two LESSONS merge them: `b` keeps both texts'
tags and `seen_in`, and `a` is superseded by `b` — the same mechanism as
`lesson add --supersedes`, so there is one way a lesson is retired. A pair marked
`--distinct` never returns to the sweep.

Nothing else is closed here. A duplicate BUG is closed only once its original is fixed,
with that original's regression test (`ddflow bug fixed`), not by linking.

## 3. Record the pass

    ddflow cadence --ran dedupe_sweep

The pass is timed by completed work (`[cadence] dedupe_sweep_every_tasks`), or by the
calendar when `[cadence] every_days` names it. Say what the pass did — `--note "settled
11 pairs, 2 lessons merged"` — so a later reader knows it was not skipped.
