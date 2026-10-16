"""Bug Bf29855c414: an add that merely NAMES existing ids is not a duplicate of them.
A [named] candidate is listed for information; only a score at the threshold refuses."""

from __future__ import annotations

import subprocess

import pytest

from ddflow import api as A
from ddflow.infra.log import EventLog

ACP = "agent client protocol transport framing, handshake and capability negotiation over stdio"
SPAWN = "process spawning supervisor restarts crashed workers with exponential backoff timers"
TEXT = "Render the quarterly burndown chart from sprint velocity and publish it to the dashboard"


@pytest.fixture(autouse=True)
def _ask(monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
    monkeypatch.setenv("DDFLOW_DEDUPE_SHOW_FLOOR", "0.5")


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    log = EventLog(tmp_path, "seed")
    for pid, body in (("P-acp", ACP), ("P-spawn", SPAWN), ("P-view", TEXT)):
        log.append("phase.added", pid, {"title": pid, "body": body, "needs": [], "globs": []})
    return tmp_path


def test_a_body_citing_three_existing_ids_is_not_refused(repo):
    body = "Reuses P-acp, P-spawn and P-view for plumbing; the new work is unrelated to them."
    out = A.phase_add(repo, "P-new", title="Telemetry export", body=body, agent="a")
    assert out.exit == 0, out
    cands = {c["id"]: c for c in out.data.get("candidates", [])}
    assert {"P-acp", "P-spawn", "P-view"} <= set(cands), "named ids stay listed as information"
    assert all("named" in c["flags"] for c in cands.values())


def test_a_real_near_duplicate_is_still_refused_even_when_it_names_ids(repo):
    body = TEXT + " (see P-acp)"
    out = A.phase_add(repo, "P-dup", title="P-view", body=body, agent="a")
    assert out.exit == 3 and "possible duplicate" in out.reason, out
    assert out.data["candidates"][0]["id"] == "P-view"


def test_a_refusal_lists_named_ids_apart_from_the_near_match(repo):
    body = TEXT + " (reuses P-acp and P-spawn)"
    out = A.phase_add(repo, "P-dup", title="P-view", body=body, agent="a")
    lines = out.reason.splitlines()
    split = next(i for i, ln in enumerate(lines) if ln.startswith("Also named"))
    assert any("P-view" in ln for ln in lines[:split])
    assert [ln for ln in lines[split + 1 :] if "P-acp" in ln or "P-spawn" in ln]
    assert not any("P-acp" in ln for ln in lines[:split])


def test_reason_keeps_a_below_floor_named_row_out_of_the_cause():
    from ddflow.api._dedupe_reason import reason

    near = {
        "id": "P-view",
        "kind": "phase",
        "state": "open",
        "score": 0.9,
        "title": "t",
        "shared": [],
        "flags": [],
    }
    cited = {**near, "id": "P-acp", "score": 0.2, "flags": ["named"]}
    lines = reason([near, cited], 0.5).splitlines()
    assert "P-view" in lines[1] and lines[2].startswith("Also named") and "P-acp" in lines[3]
    assert "Also named" not in reason([near], 0.5)


def test_the_ask_threshold_doc_agrees_with_what_a_named_id_does(repo):
    """Bug B243613aae0: the knob doc claimed naming an id asks whatever the score."""
    from ddflow.config_sections._docs import KNOB_DOCS

    doc = KNOB_DOCS["dedupe.ask_threshold"]
    low = A.phase_add(
        repo, "P-low", title="Telemetry", body="Uses P-acp for plumbing, nothing else.", agent="a"
    )
    assert low.exit == 0, low  # a named id below the threshold does not ask
    assert "naming an existing id, asks" not in doc
    assert "never refuses" in doc
