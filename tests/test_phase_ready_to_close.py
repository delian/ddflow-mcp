"""A finished phase is offered for closing by `next` and the brief (B28268eba1a).

A phase whose tasks are all done stayed OPEN, and `next` said "Nothing actionable" and
the brief "Nothing ready": the phase pipeline (its own gates, then `complete`) ran only if
someone remembered. Only `doctor` named it. Now `next` and the brief offer it, with the
commands, and never close it themselves: the phase's gates still decide.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _finished_phase(repo: Path) -> None:
    for argv in (
        ("init",),
        ("phase", "add", "P1", "--title", "p"),
        ("task", "add", "T2", "--phase", "P1", "--title", "t"),
        ("complete", "T2", "--force"),
    ):
        code, out, err = run_cli(repo, *argv, agent="a")
        assert code == 0, (argv, out, err)


def _state(repo: Path, item: str) -> str:
    code, out, _ = run_cli(repo, "show", item, agent="a")
    assert code == 0
    return out.splitlines()[1].split()[-1]


def test_next_offers_a_finished_phase_for_closing(repo):
    _finished_phase(repo)
    code, out, _ = run_cli(repo, "next", agent="a")
    assert code == 2, "no task to claim: the driver's cue for the phase close"
    assert "P1" in out, out
    assert "ddflow gate status P1" in out and "ddflow complete P1" in out, out
    assert _state(repo, "P1") == "open", "offered, never closed without its gates"


def test_next_scoped_to_the_phase_offers_it_too(repo):
    _finished_phase(repo)
    code, out, _ = run_cli(repo, "next", "--phase", "P1", agent="a")
    assert code == 2
    assert "ddflow complete P1" in out, out


def test_next_json_carries_the_finished_phases(repo):
    _finished_phase(repo)
    code, out, _ = run_cli(repo, "--json", "next", agent="a")
    assert code == 2
    assert json.loads(out)["finished_phases"] == ["P1"], out


def test_the_brief_offers_a_finished_phase_for_closing(repo):
    _finished_phase(repo)
    code, out, err = run_cli(repo, "brief", agent="a")
    assert code == 0, err
    assert "ddflow complete P1" in out, out
    assert _state(repo, "P1") == "open"


def test_a_phase_with_work_left_is_not_offered(repo):
    _finished_phase(repo)
    code, *_ = run_cli(repo, "task", "add", "T3", "--phase", "P1", "--title", "u", agent="a")
    assert code == 0
    code, out, _ = run_cli(repo, "next", agent="a")
    assert code == 0 and "T3" in out
    assert "ddflow complete P1" not in out, out
    code, out, _ = run_cli(repo, "brief", agent="a")
    assert code == 0
    assert "ddflow complete P1" not in out, out


def test_doctor_lists_the_finished_open_phase(repo):
    _finished_phase(repo)
    _, out, err = run_cli(repo, "doctor", agent="a")
    assert "P1: all 1 task(s) under it are finished but the phase is still open" in out + err


def test_mcp_next_and_brief_offer_it_too(repo, monkeypatch):
    """roborev 1494 #3: the agent surface, not only the shell."""
    import io

    from ddflow.surfaces.mcp import serve

    _finished_phase(repo)
    monkeypatch.setenv("DDFLOW_AGENT", "a")
    calls = [
        {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": t, "arguments": {}}}
        for n, t in ((1, "ddflow_next"), (2, "ddflow_brief"))
    ]
    out = io.StringIO()
    serve(repo, stdin=io.StringIO("\n".join(json.dumps(c) for c in calls) + "\n"), stdout=out)
    replies = {r["id"]: r for r in map(json.loads, out.getvalue().splitlines()) if r}
    nxt = json.dumps(replies[1]["result"])
    assert "finished_phases" in nxt and "ddflow complete P1" in nxt, nxt
    assert "ddflow complete P1" in json.dumps(replies[2]["result"])
