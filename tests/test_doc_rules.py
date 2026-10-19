"""B-dt-rules: a document's type and the rules whose globs match it reach the agent whose item
touches that document -- through the one guidance path, to the claim, the brief and a review."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import review as RVAPI
from ddflow.api._base import _load
from ddflow.services.doctypes import guidance as DG
from ddflow.services.doctypes import spec
from ddflow.services.guidance import inject as GI

TYPE = """\
[doctype]
name = "policy"
title = "Security policy"
path = "SECURITY.md"
genre = "policy"

[doctype.guidance]
audience = "someone reporting a vulnerability"
tone = "plain and calm"
style = "Google developer documentation style guide"
rules = ["Name a contact address.", "Promise a response time."]

[[doctype.section]]
id = "reporting"
heading = "Reporting"
prompt = "How does a reporter reach us?"
"""


def put_type(repo: Path, text: str = TYPE) -> None:
    d = repo / ".ddflow" / "doctypes"
    d.mkdir(parents=True, exist_ok=True)
    (d / "policy.toml").write_text(text, encoding="utf-8")


def task(repo: Path, tid: str, globs: str) -> None:
    assert run_cli(repo, "task", "add", tid, "--title", tid, "--globs", globs)[0] == 0


def test_a_guidance_table_is_parsed_checked_and_merged():
    dt = spec.parse(TYPE)
    assert dt.guidance["tone"] == "plain and calm" and dt.guidance["rules"][1].startswith("Promise")
    assert spec.parse(TYPE).to_mapping()["guidance"] == dict(dt.guidance)
    with pytest.raises(spec.DocTypeError, match="unknown key 'voice' in guidance"):
        spec.parse(TYPE.replace("tone =", "voice ="), source="p.toml")
    with pytest.raises(spec.DocTypeError, match="tone must be str"):
        spec.parse(TYPE.replace('tone = "plain and calm"', "tone = 3"))
    child = spec.resolve(
        [
            ("shipped", [("p.toml", TYPE)]),
            (
                "project",
                [
                    (
                        "q.toml",
                        '[doctype]\nname = "policy"\nextends = "policy"\n[doctype.guidance]\ntone = "warm"\n',
                    )
                ],
            ),
        ]
    )["policy"]
    assert child.guidance["tone"] == "warm" and child.guidance["audience"].startswith("someone")


def test_a_type_becomes_one_record_plus_one_per_rule_scoped_to_its_documents():
    recs = DG.records_of(spec.parse(TYPE, source="project/policy.toml"))
    assert [r.id for r in recs] == ["dt-policy", "dt-policy-r1", "dt-policy-r2"]
    first = recs[0]
    assert first.scope.globs == ("SECURITY.md",) and "Audience: someone reporting" in first.body
    assert "- Reporting: How does a reporter reach us?" in first.body
    assert recs[1].body == "Name a contact address." and recs[1].sources == ["project/policy.toml"]
    assert GI.origin_of(first).trust == "agent"
    shipped = DG.records_of(spec.parse(TYPE, source="shipped/policy.toml"))[0]
    assert GI.origin_of(shipped).trust == "operator"


def test_a_type_with_nothing_to_say_adds_nothing():
    bare = '[doctype]\nname = "t"\npath = "T.md"\n'
    assert DG.records_of(spec.parse(bare)) == []


def test_broken_type_files_never_fail_the_lookup(tmp_path: Path):
    (tmp_path / ".ddflow" / "doctypes").mkdir(parents=True)
    (tmp_path / ".ddflow" / "doctypes" / "bad.toml").write_text("[[[", encoding="utf-8")
    assert DG.records(tmp_path) == [] and DG.records(None) == []


def test_a_claim_on_a_document_gets_its_types_guidance_and_one_on_code_does_not(repo: Path):
    put_type(repo)
    task(repo, "DOC", "SECURITY.md")
    task(repo, "CODE", "src/app.py")
    out = run_cli(repo, "claim", "DOC", "--no-worktree")[1]
    assert "## Guidance that governs this item" in out
    assert 'id="dt-policy"' in out and "Promise a response time." in out
    assert 'id="dt-policy"' not in run_cli(repo, "claim", "CODE", "--no-worktree")[1]


def test_a_rule_on_docs_reaches_an_item_touching_docs_and_not_one_touching_src(repo: Path):
    rule = ["rule", "add", "--id", "r-prose", "--title", "prose", "--content", "short sentences"]
    assert run_cli(repo, *rule, "--globs", "docs/**/*.md")[0] == 0
    task(repo, "DOCS", "docs/a.md")
    task(repo, "SRC", "src/a.py")
    assert 'id="r-prose"' in run_cli(repo, "brief", "--item", "DOCS")[1]
    assert 'id="r-prose"' not in run_cli(repo, "brief", "--item", "SRC")[1]
    assert 'id="r-prose"' in run_cli(repo, "claim", "DOCS", "--no-worktree")[1]


def test_a_review_of_a_document_is_sent_its_types_rules_to_verify_against(repo: Path):
    put_type(repo)
    task(repo, "DOC", "SECURITY.md")
    _log, cfg, st = _load(repo, "a1")
    said: list[str] = []
    ctx = RVAPI._with_guidance("ctx", cfg, st, "DOC", "doc_review", said.append)
    assert "Verify the change complies" in ctx and 'id="dt-policy-r1"' in ctx
    assert any("dt-policy" in line for line in said)
