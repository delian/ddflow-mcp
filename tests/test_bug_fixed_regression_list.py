"""`bug fixed` takes SEVERAL regression tests (bug B227585c781).

A fix guarded by more than one test could not say so. A ';'-joined value was resolved as
ONE node id and refused as "names a test that exists in no worktree: <the whole list>",
which misdiagnosed the input; a repeated `--regression-test` silently kept only the last
one. Now the flag repeats, a value splits on ',' and ';', MCP takes a list as well as the
string, every test is resolved, and a refusal names only the tests that are missing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server

OK, FAIL = 0, 1

SUITE = """
import pytest


def test_a():
    pass


def test_b():
    pass


@pytest.mark.parametrize("n", ["x;y", "1,2"])
def test_p(n):
    pass
"""

A, B = "tests/test_fix.py::test_a", "tests/test_fix.py::test_b"


def _setup(repo: Path) -> None:
    run_cli(repo, "init")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_fix.py").write_text(SUITE)
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")


def _fixed_events(repo: Path) -> list[dict]:
    return [e.data for e in EventLog(repo).read_all() if e.kind == "bug.fixed"]


def test_semicolon_separated_tests_close_the_bug_and_are_all_recorded(repo):
    _setup(repo)
    out = api.bug_fixed(repo, "B1", regression_test=f"{A}; {B}")
    assert out.exit == OK, out.reason
    (data,) = _fixed_events(repo)
    assert data["regression_tests"] == [A, B]
    assert data["regression_test"] == f"{A}; {B}", "one string is kept as given"


def test_a_repeated_cli_flag_records_every_test_not_just_the_last(repo):
    _setup(repo)
    code, out, err = run_cli(
        repo, "bug", "fixed", "B1", "--regression-test", A, "--regression-test", B
    )
    assert code == OK, err
    (data,) = _fixed_events(repo)
    assert data["regression_tests"] == [A, B]
    assert A in out and B in out


def test_a_missing_test_in_a_list_is_named_alone(repo):
    _setup(repo)
    gone = "tests/test_fix.py::test_gone"
    out = api.bug_fixed(repo, "B1", regression_test=f"{A};{gone};{B}")
    assert out.exit == FAIL, out
    assert gone in out.reason
    assert A not in out.reason and B not in out.reason, out.reason
    assert not _fixed_events(repo)


def test_tests_joined_by_whitespace_are_diagnosed_as_several_not_as_one_missing_test(repo):
    _setup(repo)
    out = api.bug_fixed(repo, "B1", regression_test=f"{A} {B}")
    assert out.exit == FAIL, out
    assert "several" in out.reason and ";" in out.reason, out.reason


def test_a_separator_inside_a_parametrize_id_does_not_split(repo):
    _setup(repo)
    out = api.bug_fixed(
        repo, "B1", regression_test="tests/test_fix.py::test_p[x;y]; tests/test_fix.py::test_p[1,2]"
    )
    assert out.exit == OK, out.reason
    (data,) = _fixed_events(repo)
    assert data["regression_tests"] == [
        "tests/test_fix.py::test_p[x;y]",
        "tests/test_fix.py::test_p[1,2]",
    ]


def test_mcp_takes_a_list_and_still_takes_the_string(repo):
    _setup(repo)
    run_cli(repo, "bug", "found", "--id", "B2", "--summary", "y")
    srv = Server(repo)

    def call(**args) -> dict:
        reply = srv.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_bug_fixed", "arguments": args},
            }
        )
        return reply["result"]

    run_cli(repo, "bug", "found", "--id", "B3", "--summary", "z")
    run_cli(repo, "bug", "found", "--id", "B4", "--summary", "w")
    p12 = "tests/test_fix.py::test_p[1,2]"
    listed = call(id="B1", regression_tests=[A, p12])
    assert not listed.get("isError"), listed
    single = call(id="B2", regression_test=A)
    assert not single.get("isError"), single
    raw_list = call(id="B3", regression_test=[A, B])  # a client that ignores the schema
    assert not raw_list.get("isError"), raw_list
    events = _fixed_events(repo)
    assert [e["regression_tests"] for e in events] == [[A, p12], [A], [A, B]]
    assert events[1]["regression_test"] == A
    assert json.loads(listed["content"][0]["text"])["id"] == "B1"
    neither = call(id="B4")
    assert neither.get("isError") and "regression-test" in json.dumps(neither), neither


def test_mcp_schema_offers_the_list_form():
    from ddflow.surfaces.mcp import TOOLS, _schema

    props = _schema(TOOLS["ddflow_bug_fixed"])["properties"]
    assert props["regression_tests"] == {**props["regression_tests"], "type": "array"}
    assert props["regression_tests"]["items"] == {"type": "string"}


def test_a_blank_repeated_flag_is_still_no_regression_test(repo):
    """The CLI now always passes a list; a whitespace-only value must not slip past the
    required-test rule as a truthy string."""
    _setup(repo)
    code, _out, err = run_cli(repo, "bug", "fixed", "B1", "--regression-test", "   ")
    assert code == FAIL and "regression-test" in err, err
    out = api.bug_fixed(repo, "B1", regression_test=["  ", " ; , "])
    assert out.exit == FAIL, out
    assert not _fixed_events(repo)


def test_tests_joined_by_whitespace_after_a_parametrize_id_are_refused_with_the_hint(repo):
    """`test_p[1,2] other` resolved `test_p` and accepted the rest unchecked."""
    _setup(repo)
    out = api.bug_fixed(repo, "B1", regression_test=f"tests/test_fix.py::test_p[1,2] {B}")
    assert out.exit == FAIL, out
    assert "several" in out.reason, out.reason
