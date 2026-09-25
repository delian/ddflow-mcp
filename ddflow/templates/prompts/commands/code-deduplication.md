Find and remove duplicated logic{% if scope %} in {{ scope }}{% endif %}.

## Start from a mechanical report, not from intuition

Reading code to find duplication finds the duplication you happen to read. Run a clone detector first if the project has one (`jscpd`, `pmd cpd`, `similarity-py`, …) and work from its `file:line` pairs. `ddflow_loops` also reports `duplicate_work`: two live queue items declaring the same files, which is duplication about to be written.

Then grep by **operation**, not by the name you would have invented: "retry with backoff", "atomic write", "parse duration", "release the lock". Duplication is almost never a decision — it is what happens when nobody looked first.

## The rule for acting

- **2nd occurrence:** reuse the existing one, or extract.
- **3rd occurrence:** extraction is mandatory.
- **1st occurrence:** do nothing. A one-caller abstraction is its own defect.

## Prefer elimination over guarding

Two implementations of one operation will drift; they always have. Deleting one is worth more than adding a test that asserts they agree — the test is a second thing to maintain and it only fires *after* they have already diverged.

Where the copies have already drifted, the difference is the finding: one of them is a bug fix the other never got.

## Extending beats copy-and-tweak

When existing code almost fits, add the parameter or the hook and call it. Copy-and-tweak is precisely how two copies begin.

## Then

1. Run the tests. An extraction that changes behaviour is a refactor that failed, and the only way to know is to run them.
2. `ddflow_gate_record <item> dedupe --outcome passed --evidence "<what was merged, what the detector said before and after>"`.
3. `ddflow_lesson_add` if the duplication had a cause worth naming — a missing seam, an unclear owner, a helper nobody could find.
