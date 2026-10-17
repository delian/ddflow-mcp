"""`ddflow show <research id>` prints the note and what was filed `--extends` it
(bug research-note-not-showable-by-id)."""

from __future__ import annotations

import json

from conftest import run_cli


def _note(repo, rid, *extra, claim="the claim"):
    return run_cli(
        repo, "research", "add", "--id", rid, "--question", f"q {rid}", "--claim", claim,
        "--verdict", "CONFIRMED", "--probe", "p", "--probe-output", "o", *extra,
    )  # fmt: skip


def test_show_resolves_a_research_note_id(repo):
    run_cli(repo, "init")
    assert _note(repo, "R-one", "--new")[0] == 0
    code, out, err = run_cli(repo, "show", "R-one")
    assert code == 0, err
    assert "R-one [research] CONFIRMED" in out and "the claim" in out and "q R-one" in out


def test_show_prints_the_extension_history(repo):
    run_cli(repo, "init")
    assert _note(repo, "R-one", "--new")[0] == 0
    assert _note(repo, "R-two", "--extends", "R-one", claim="a narrower claim")[0] == 0
    out = run_cli(repo, "show", "R-one")[1]
    assert "R-two" in out and "a narrower claim" in out


def test_show_json_for_a_research_note(repo):
    run_cli(repo, "init")
    assert _note(repo, "R-one", "--new")[0] == 0
    code, out, err = run_cli(repo, "--json", "show", "R-one")
    assert code == 0, err
    body = json.loads(out)
    assert body["kind"] == "research" and body["claim"] == "the claim"
