"""`bug fixed --lint-exempt`: closing a CI-filed LINT bug, which has no test to write (Bee3becc481).

Split from `bug_close` so neither module grows past the area-size ratchet."""

from __future__ import annotations

import re

from ...core import outcome as O
from .bugs import _unknown_bug


def _is_node_id(test: str) -> bool:
    """A pytest node id of a python file: ``path/to/test_x.py::test_name``."""
    return "::" in test and test.split("::", 1)[0].endswith(".py")


_CI_LINT_TITLE = re.compile(r"^\S+ CI check failed: (?P<check>.+?)\s*$")


def _lint_check(cfg, st, item: str) -> str:
    """The check id of a CI-filed LINT bug (not a pytest node id; it names one of
    `[bugs].lint_checks`), else ""."""
    bug = st.bugs.get(item)
    m = _CI_LINT_TITLE.search(bug.title) if bug is not None else None
    if not m or _is_node_id(m["check"]):  # a pytest node id is a test: it needs its test
        return ""
    low = m["check"].lower()
    return m["check"] if any(w.lower() in low for w in cfg.bugs.lint_checks) else ""


def _close_lint_exempt(
    log, cfg, st, item, reason, tests, changelog_entry, *, takes_no, record
) -> O.Outcome:
    """`bug fixed --lint-exempt REASON`: the close of a CI lint bug that has no test to write."""
    if item not in st.bugs:
        return _unknown_bug("bug.fixed", item, st)
    check = _lint_check(cfg, st, item)
    if not check:
        return O.refused(
            "bug.fixed",
            f"--lint-exempt is for a CI-filed lint bug (a check that is not a test); {item} is "
            "not one. Name the regression test that guards it.",
            id=item,
        )
    if not reason.strip():
        return O.refused(
            "bug.fixed", "--lint-exempt needs the reason (recorded): what made it green", id=item
        )
    if tests:
        return O.refused(
            "bug.fixed", "--lint-exempt and --regression-test are alternatives; give one", id=item
        )
    if takes_no:
        why = f"--lint-exempt closes without a test; it takes no {', '.join(takes_no)}"
        return O.refused("bug.fixed", why, id=item)
    payload = {
        "regression_test": "",
        "regression_tests": [],
        "lesson": "",
        "regression_verified": "exempt-lint",
        "regression_verify": {"lint_check": check, "reason": reason.strip()},
        **({"changelog": changelog_entry} if changelog_entry else {}),
    }
    record(log, item, payload, None)
    return O.ok(
        "bug.fixed",
        id=item,
        regression_test="",
        regression_tests=[],
        regression_verified="exempt-lint",
        regression_verify=payload["regression_verify"],
    )
