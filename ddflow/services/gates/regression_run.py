"""What a regression-test run proves (Bregtest-skip-counted-as-pass).

`verify_regression_test` read the gate's outcome alone: pytest exits 0 for a test that was
SKIPPED or xfail, and non-zero for one that was never collected or that errored in a fixture.
So a skip on the pre-fix tree "passed" (refusing a valid guard), a skip on the fixed tree
"passed" (proving nothing), and an uncollected or erroring test "failed before" (a guard
that never ran). Only a test that ran counts: it FAILED on the pre-fix tree, it PASSED on
the fixed one. Anything else is a gap with its own words, never a verdict on the test.

The counts are read from the runner's own summary line; a run that printed none keeps the
outcome's verdict (the project's runner is then not one this module can read).
"""

from __future__ import annotations

from typing import Any

from .testcmd import counts_of

#: pytest exit codes that mean no test ran: usage error (4), nothing collected (5).
NO_TEST_RAN = (4, 5)
_NOT_RUN = ("skipped", "xfailed", "xpassed")


def _counts(ev: dict[str, Any]) -> dict[str, int]:
    lines = ev.get("summary") or []
    return counts_of(str(lines[-1])) if lines else {}


def _said(counts: dict[str, int]) -> str:
    return ", ".join(f"{counts[k]} {k}" for k in (*_NOT_RUN, "error") if counts.get(k))


def not_a_pass(ev: dict[str, Any], *, where: str, strict: bool) -> str:
    """Why a run that exited 0 did not PASS the test ("" when it did): it ran no test, or
    skipped / xfailed it. ``strict``: any skip counts (the fixed tree must pass every named
    test); otherwise only a run where nothing passed (a test that did pass on the pre-fix
    tree is refused as such)."""
    counts = _counts(ev)
    skipped = sum(counts.get(k, 0) for k in _NOT_RUN)
    if not counts or not (skipped and (strict or not counts.get("passed"))):
        return ""
    return (
        f"the named test did not run to a pass on {where}: {_said(counts)} "
        f"(skipped or xfail is not a pass), so there is no evidence either way"
    )


def not_a_failure(ev: dict[str, Any], *, where: str) -> str:
    """Why a run that exited non-zero did not FAIL the test ("" when it did): no test was
    collected, or it errored (a fixture, a collection error) rather than failed."""
    code = ev.get("exit")
    if code in NO_TEST_RAN:
        return (
            f"no test ran on {where} (pytest exited {code}: the named test was not "
            f"collected), so it did not fail before the fix"
        )
    if isinstance(code, int) and code != 1 and not _counts(ev).get("failed"):
        # 2 interrupted / collection error, 3 internal: a test that DID fail first still counts
        return (
            f"the run on {where} ended without a test failure (pytest exited {code}: "
            f"interrupted or an internal error), so the test is not shown to fail before the fix"
        )
    counts = _counts(ev)
    if counts and not counts.get("failed"):
        what = _said(counts) or "no test failed"
        return (
            f"the named test did not FAIL on {where}: {what} (an error is not a failure "
            f"of the test's assertion), so it is not shown to fail before the fix"
        )
    return ""
