"""`show <bug>`'s prose, branch by branch (B-uc-surf-reporting).

`_bug_lines` is a pure function over the bug's body; the golden project pins one bug, and
this pins every line it can print: the severity/scope marks, the upstream report in its
three shapes, the closure by fix (one test, several, none), the closure as invalid (with and
without evidence), a fix that wins over an invalid closure, and the addenda block.
Recorded against the function before it was split.
"""

from __future__ import annotations

import pytest

from ddflow.surfaces.commands.reporting import _bug_lines

BASE = {
    "id": "B1", "state": "open", "title": "", "item": "", "found_at": "2026-01-01",
    "severity": "", "scope": "project", "upstream_sent_at": "", "fixing": [],
    "mentioned_by": [], "fixed_at": "", "regression_tests": [], "regression_test": "",
    "invalid_at": "", "invalid_reason": "", "evidence": "", "lesson": "", "summary": "Sum",
    "additions": [], "links": [], "dismissals": [], "linked_from": [],
}  # fmt: skip
ADDITIONS = [{"at": "t1", "who": "a", "text": "line1\nline2"}]
FIXED = {"state": "fixed", "fixed_at": "2026-01-03"}
INVALID = {"state": "invalid", "invalid_at": "2026-01-04", "invalid_reason": "dup"}
CASES = {
    "plain": ({}, "B1 [bug] open\n  found 2026-01-01\n\nSum"),
    "full_open": (
        {
            "title": "T",
            "item": "T1",
            "severity": "high",
            "scope": "upstream",
            "upstream_sent_at": "2026-01-02",
            "upstream_url": "http://x",
            "fixing": ["F1", "F2"],
            "mentioned_by": ["T3"],
            "lesson": "L1",
            "additions": ADDITIONS,
        },
        "B1 [bug] open\n  title T\n  severity high; scope upstream\n  found 2026-01-01 on T1\n  reported upstream 2026-01-02: http://x\n  fix task(s): F1, F2\n  mentioned by: T3\n  lesson L1\n\nSum\n  additions (1):\n    t1 by a\n      | line1\n      | line2",
    ),
    "upstream_delivery": (
        {"upstream_sent_at": "2026-01-02", "upstream_delivery": "mail"},
        "B1 [bug] open\n  found 2026-01-01\n  reported upstream 2026-01-02: mail\n\nSum",
    ),
    "upstream_bare": (
        {"upstream_sent_at": "2026-01-02"},
        "B1 [bug] open\n  found 2026-01-01\n  reported upstream 2026-01-02: upstream\n\nSum",
    ),
    "fixed_tests": (
        {**FIXED, "regression_tests": ["t/a.py::x", "t/b.py::y"]},
        "B1 [bug] fixed\n  found 2026-01-01\n  closed 2026-01-03 as fixed; regression test(s):\n    t/a.py::x\n    t/b.py::y\n\nSum",
    ),
    "fixed_single": (
        {**FIXED, "regression_test": "t/c.py::z"},
        "B1 [bug] fixed\n  found 2026-01-01\n  closed 2026-01-03 as fixed; regression test(s):\n    t/c.py::z\n\nSum",
    ),
    "fixed_none": (
        {**FIXED},
        "B1 [bug] fixed\n  found 2026-01-01\n  closed 2026-01-03 as fixed\n\nSum",
    ),
    "invalid": (
        {**INVALID, "evidence": "see B9"},
        "B1 [bug] invalid\n  found 2026-01-01\n  closed 2026-01-04 as invalid: dup\n    evidence: see B9\n\nSum",
    ),
    "invalid_noevid": (
        {**INVALID},
        "B1 [bug] invalid\n  found 2026-01-01\n  closed 2026-01-04 as invalid: dup\n\nSum",
    ),
    "fixed_over_invalid": (
        {
            **FIXED,
            "regression_test": "t/c.py::z",
            "invalid_at": "2026-01-04",
            "invalid_reason": "dup",
            "evidence": "e",
        },
        "B1 [bug] fixed\n  found 2026-01-01\n  closed 2026-01-03 as fixed; regression test(s):\n    t/c.py::z\n  earlier closed 2026-01-04 as invalid: dup -- superseded by the fix\n    evidence: e\n\nSum",
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_a_bug_is_told_line_by_line(name):
    changes, expected = CASES[name]
    assert _bug_lines({**BASE, **changes}) == expected
