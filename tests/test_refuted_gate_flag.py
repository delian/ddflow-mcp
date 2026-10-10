"""D-unify 5: `ddflow gate list --refuted [--since]` -- the operator's spot-check narrows to
the passes recorded at or after a date, on the CLI and over MCP.

The flag itself (gate status, status, brief, the completion result) is pinned by
tests/test_gate_list_refuted.py and tests/test_gate_econ_review_refutation.py.
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli
from test_gate_list_refuted import _flag

import ddflow.api as A


@pytest.fixture(autouse=True)
def _full_rounds(monkeypatch):
    monkeypatch.setenv("DDFLOW_REVIEW_DELTA_DEFAULT", "0")


def test_since_keeps_passes_at_or_after_it_and_each_row_has_its_time(repo, tmp_path):
    _flag(repo, tmp_path)
    [row] = A.gate_list(repo, refuted=True).data["passes"]
    assert row["at"].startswith("20")
    day = row["at"][:10]
    assert A.gate_list(repo, refuted=True, since=day).data["count"] == 1
    assert A.gate_list(repo, refuted=True, since=row["at"]).data["count"] == 1
    assert A.gate_list(repo, refuted=True, since="2999-01-01").data["count"] == 0
    assert (
        "no gate was passed on refutation"
        in A.gate_list(repo, refuted=True, since="2999-01-01").data["text"]
    )


def test_since_on_the_cli_and_in_json(repo, tmp_path):
    _flag(repo, tmp_path)
    code, out, _err = run_cli(repo, "gate", "list", "--refuted", "--since", "2000-01-01")
    assert code == 0 and out.startswith("T1.critic  1 refuted")
    code, out, _err = run_cli(repo, "--json", "gate", "list", "--refuted", "--since", "2999-01-01")
    assert code == 0 and json.loads(out)["count"] == 0


def test_since_without_refuted_is_refused_not_ignored(repo, tmp_path):
    _flag(repo, tmp_path)
    out = A.gate_list(repo, since="2000-01-01")
    assert out.exit != 0 and "--refuted" in out.reason
    code, _out, err = run_cli(repo, "gate", "list", "--since", "2000-01-01")
    assert code != 0 and "--refuted" in err


def test_the_mcp_tool_takes_since(repo, tmp_path):
    from ddflow.surfaces.tools import TOOLS

    _flag(repo, tmp_path)
    tool = TOOLS["ddflow_gate_list"]
    assert "since" in tool["properties"]
    assert tool["api"](repo, {"refuted": True, "since": "2000-01-01"}, "").data["count"] == 1
    assert tool["api"](repo, {"refuted": True, "since": "2999-01-01"}, "").data["count"] == 0


def test_since_compares_instants_not_strings(monkeypatch):
    """The reviewer's case: a time with an offset, or a Z, is the same instant however it
    is spelled; a raw string compare kept or dropped the wrong passes."""
    from types import SimpleNamespace as NS

    from ddflow.api import gates as api_gates
    from ddflow.core.records import GateRecord

    rec = GateRecord(
        "critic",
        "passed",
        at="2024-01-02T10:00:00.000000Z",
        evidence={"passed_on_refutation": {"refuted": 1, "confirmed": 0, "rounds": 2}},
    )
    st = NS(items={"T": NS(id="T", title="t", state="done", removed=False, gates={"critic": rec})})
    monkeypatch.setattr(api_gates, "_load", lambda repo, agent: (None, None, st))

    def n(since):
        return api_gates.list_gates(None, refuted=True, since=since).data["count"]

    assert n("2024-01-02T11:00:00+02:00") == 1  # 09:00Z: before the pass (strings: dropped)
    assert n("2024-01-02T10:00:00+00:00") == 1  # the same instant: included (strings: '+' < 'Z')
    assert n("2024-01-02T10:00:01Z") == 0
    assert n("2024-01-02T11:00:01+01:00") == 0  # 10:00:01Z
    assert n("2024-01-02") == 1 and n("2024-01-03") == 0
    assert n("") == 1


def test_an_unparseable_since_is_refused_with_a_reason(repo, tmp_path):
    _flag(repo, tmp_path)
    out = A.gate_list(repo, refuted=True, since="yesterday")
    assert out.exit != 0 and "ISO" in out.reason
