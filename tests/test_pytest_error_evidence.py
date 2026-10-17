"""A pytest ERROR (fixture setup) is shown as an error, with its message and fixture (Bb9ce40cfcc).

`ci` ended `12421 passed, 1 error` and printed `FAILED tests/...::test_...` with nothing else:
for a long test name pytest writes the section header as `_ ERROR at setup of <name> _` (one
underscore a side, no room for more) and drops ` - message` from the summary line, so neither
the section nor a reason was read. The output below is a real pytest run, COLUMNS=80.
"""

from __future__ import annotations

from ddflow.services import flakes as F

LONG = "test_the_apply_step_describes_what_held_in_a_clean_rehearsal_and_nothing_else_at_all_x"
OUT = f"""E                                                                        [100%]
==================================== ERRORS ====================================
_ ERROR at setup of {LONG} _

    @pytest.fixture
    def boom():
>       raise RuntimeError("fixture exploded: no port")
E       RuntimeError: fixture exploded: no port

test_longnames.py:4: RuntimeError
=========================== short test summary info ============================
ERROR test_longnames.py::{LONG}
"""
TID = f"test_longnames.py::{LONG}"


def test_the_error_section_of_a_long_named_test_is_read():
    ev = F.failure_evidence(OUT)
    assert [t["test"] for t in ev["failure_tails"]] == [f"ERROR at setup of {LONG}"]
    assert "fixture exploded: no port" in ev["failure_tails"][0]["tail"]


def test_an_error_is_listed_apart_from_failures_with_its_message_and_fixture():
    ev = F.failure_evidence(OUT)
    assert ev["errored_tests"] == [TID]
    assert ev["failed_reasons"][TID] == "RuntimeError: fixture exploded: no port"
    assert ev["errored_where"][TID] == {"phase": "setup", "fixture": "boom"}
    text = F.failure_text(ev)
    assert "FAILED" not in text
    assert f"ERROR {TID}" in text and "setup of fixture boom" in text
    assert "RuntimeError: fixture exploded: no port" in text


def test_failures_and_errors_are_counted_and_shown_in_their_own_groups():
    out = OUT.replace(
        "=========================== short test summary info",
        "=================================== FAILURES ===================================\n"
        "____________________________________ test_b ____________________________________\n\n"
        ">       assert 1 == 2\nE       assert 1 == 2\n\n"
        "=========================== short test summary info",
    ) + "FAILED test_x.py::test_b - assert 1 == 2\n"
    text = F.failure_text(F.failure_evidence(out))
    assert "1 failing test(s):" in text and "FAILED test_x.py::test_b - assert 1 == 2" in text
    assert "1 error(s)" in text
    assert text.index("FAILED test_x.py::test_b") < text.index(f"ERROR {TID}")
