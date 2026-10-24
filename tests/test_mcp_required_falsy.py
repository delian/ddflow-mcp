"""A required argument passed as a falsy-but-present value (false, 0, "") is present,
not missing: `_check_call` must test key presence / None, not truthiness
(B-mcp-required-falsy)."""

from __future__ import annotations

from ddflow.surfaces.mcp import TOOLS, Server


def _spec():
    return {
        "properties": {
            "flag": ("boolean", "a required boolean", True),
            "count": ("integer", "a required integer", True),
        }
    }


def test_required_boolean_false_is_present_not_missing():
    err = Server._check_call("t", _spec(), {"flag": False, "count": 1}, None)
    assert err == "", err


def test_required_number_zero_is_present_not_missing():
    err = Server._check_call("t", _spec(), {"flag": True, "count": 0}, None)
    assert err == "", err


def test_required_empty_string_is_present_not_missing():
    spec = {"properties": {"name": ("string", "a required string", True)}}
    err = Server._check_call("t", spec, {"name": ""}, None)
    assert err == "", err


def test_an_absent_required_argument_is_still_missing():
    err = Server._check_call("t", _spec(), {"count": 2}, None)
    assert "missing required" in err and "flag" in err, err


def test_an_explicit_none_required_argument_is_still_missing():
    err = Server._check_call("t", _spec(), {"flag": None, "count": 2}, None)
    assert "missing required" in err and "flag" in err, err


def test_phase_close_with_imported_false_is_sendable():
    err = Server._check_call(
        "ddflow_phase_close", TOOLS["ddflow_phase_close"], {"id": "P1", "imported": False}, None
    )
    assert err == "", err
