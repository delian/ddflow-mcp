# Test-impact selection: probe of the existing selector

Item: TS-impact-probe (re-scoped). Replay: the newest 100 `merge <item>:` commits of main
(85 changed source files), each against the EXISTING selector in `services/testselect.py` with
the project's `[gates]` defaults. Reproduce: `uv run python tests/test_testselect_probe.py 100`.

## Claim

Selecting by import closure with the hub fallback keeps recall of the tests a merge changed
next to its source, at a small share of the suite. Falsifier: recall of routable tests below
the 0.85 floor, or a median share near 1.

## Result (2026-11-12)

| measure | value |
|---|---|
| merges replayed (with source) | 85 (72 scored: a test changed beside the source) |
| recall, all co-changed tests | 0.683 (about 94 of 138 co-changed tests held) |
| recall, routable (subprocess-driven tests set aside) | 0.864 |
| median selected share of the suite | 0.022 |
| whole-suite answer rate | 0.071 (6 merges: 5 changed hubs, 1 test infrastructure) |
| hub-fallback rate | 0.059 (5 merges) |
| unrouted rate (source changed, nothing selected, no fallback) | 0.059 (5 merges) |
| subprocess-blind misses | 29 tests |
| hub merges: choice with the hub bound lifted | median share 0.074, recall 0.900 |

Verdict: CONFIRMED for fast feedback (median 2 percent of the suite), with two gaps that the
unit_tests/ci whole run must keep covering:

1. Recall is 0.683 overall (about 94 of 138 co-changed tests); the 44 missed tests split into 29 that drive the program in a
   subprocess (no import route: what TS-cover-deps' recorded dependencies add) and about
   20 with a route the selector did not draw (re-exporting `__init__`, template/doc text
   readers, farther than two hops).
2. Unmapped source changes select nothing (5.9 percent of merges), rather than the whole
   suite. Selection by itself is not a safe gate for them; a gate that relies on it must
   treat "no route" as a fallback. TS-impact-select owns that falsifier.

## Hub fallback quality (feeds TS-impact-select)

At a hub the selector answers the whole suite. Lifting only the hub bound selects a median
7.4 percent of the suite and holds 90 percent of the co-changed tests; the remaining 10
percent is the reason the whole-suite answer exists. A narrower, still safe choice must add
the hub's direct importers plus recorded dependencies, and expand to the whole suite when
unsure.

## Not measured

The full suite at -n 48 once per merge (comparing failing ids) was not run: 85 whole runs
are hours of load on a shared, baseline-red main. Recall here is the co-changed-tests proxy
the recall guard in `tests/test_testselect_recall.py` already uses.
