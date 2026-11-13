# Review contract

The reviewers and the author share one contract. `ddflow review <item>` runs the reviewers on
their configured models; this page is what both sides may assume about a round, and where a
round stops. It is a contract, not advice: a result that breaks it is a finding for the review
of the fix, the same as if it were a defect in the code.

## A round

* A round returns **only new findings**, each with an id. A finding already raised and not
  fixed is quoted by its id, never re-argued. A finding already closed (`ddflow review triage
  <item> --gate <gate> --finding N --refuted|--confirmed --probe ...`) is settled: quoting it
  again returns the same id, which reads as that closed finding, not a new one.
* A finding's id is its full text: the same words are the same finding, so a triage of one is a
  triage of both. Two findings written differently are two findings, whatever their file names
  look like.
* The author **batches fixes per round**: one round's fixes answer one round's findings. A
  reviewer judging a batched fix is judging the differences, not re-reading every round before
  it — closed findings are not repeated because there is nothing to add.

## Where a round stops

* Round 4 is the stop rule. `[review].max_rounds` is 4; a fifth round runs only when the
  operator names it. After the fourth round the gate settles on triage and the standing
  findings are recorded, not litigated.
* A result with no `STATUS:` block is **off contract** and reviews nothing; it is a coverage
  gap, not a verdict. A truncated or clipped answer is the same: it is recorded, never counted
  as a clean review.

## The checker

The engine is the checker the brief names. `ddflow.services.review.engine.parse` turns a
round's reply into findings and reports `on_contract` from its `STATUS:` block, and
`finding_digest` is a finding's id. A reply whose `STATUS:` block is missing is flagged off
contract; a finding word-for-word identical to a closed one has the same id, so a repeated
one is seen as that closed finding, not a new one to re-litigate.
