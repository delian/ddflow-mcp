"""Golden: what `ddflow search`, `ddflow rule search` and the skills ranking return for a
fixed project, byte for byte (B-uni-search-core; D-unify 4).

The search engine is being unified behind one core (`services/searchcore/`); these pin
today's rows, order, scores and refusals so a move behind the core leaves them unchanged.
"""

# ruff: noqa: F811 -- the goldenfix fixtures are imported, then named as parameters
from __future__ import annotations

from pathlib import Path

import pytest
from goldenfix import _pinned_environment, ddflow, project  # noqa: F401 -- fixtures

from ddflow.services import skills as SK

SEARCHES = {
    "ranked parser": ["search", "parser grammar"],
    "ranked tokenizer": ["search", "tokenizer comment"],
    "ranked json": ["--json", "search", "recursive descent parser"],
    "ranked kind filter": ["search", "parser", "--kind", "task,lesson"],
    "ranked limit": ["search", "parser", "--limit", "1"],
    "exact": ["search", "--exact", "trailing comment"],
    "regex": ["search", "--regex", "pars(er|ing)"],
    "regex unsafe": ["search", "--regex", "(a+)+b"],
    "regex backref": ["search", "--regex", r"(a)\1"],
    "stop words only": ["search", "the and of"],
    "nothing": ["search", "zzzqqqxxx"],
    "bad kind": ["search", "parser", "--kind", "nope"],
    "log": ["search", "parser", "--kind", "log"],
}

RULES = [
    ("r-style", "Keep functions short", "Prefer small functions with one purpose", "style"),
    ("r-tests", "Write a failing test first", "A bug needs a regression test", "testing"),
    ("r-naming", "Name things plainly", "Use plain names for functions and tests", "style"),
]
RULE_SEARCHES = {
    "rule ranked": ["rule", "search", "small functions"],
    "rule ranked fallback": ["rule", "search", "plain tests names"],
    "rule exact": ["rule", "search", "--exact", "regression test"],
    "rule regex": ["rule", "search", "--regex", "plain|short"],
    "rule substring": ["rule", "search", "functions"],
    "rule tag": ["rule", "search", "functions", "--tag", "style"],
    "rule none": ["rule", "search", "zzzqqqxxx"],
    "rule bad regex": ["rule", "search", "--regex", "("],
}


@pytest.mark.parametrize("name", list(SEARCHES))
def test_search(name, ddflow, snapshot):
    assert ddflow(*SEARCHES[name]) == snapshot


@pytest.mark.parametrize("name", list(RULE_SEARCHES))
def test_rule_search(name, ddflow, snapshot):
    for rid, title, content, tag in RULES:
        code, _ = ddflow("rule", "add", "--id", rid, "--title", title, "--content", content,
                         "--tags", tag, "--new")  # fmt: skip
        assert code == 0
    assert ddflow(*RULE_SEARCHES[name]) == snapshot


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


QUERIES = ["deploy the release", "write a test", "cursor rule naming", "zzzqqq", "agent"]


def test_skills_rank(tmp_path, snapshot):
    _write(tmp_path, ".claude/skills/deploy/SKILL.md",
           "---\nname: deploy\ndescription: Deploy the release to production\n---\nRun the deploy script.\n")  # fmt: skip
    _write(tmp_path, ".claude/skills/tester/SKILL.md",
           "---\nname: tester\ndescription: Write a test for a change\n---\nA failing test first.\n")  # fmt: skip
    _write(tmp_path, ".claude/commands/release.md", "# Cut a release\nTag and deploy the release.\n")  # fmt: skip
    _write(tmp_path, ".cursor/rules/naming.mdc", "Name things plainly. Cursor rule on naming.\n")
    _write(tmp_path, "AGENTS.md", "# Agents\nHow an agent should work here.\n")
    inv = SK.inventory(tmp_path)
    assert [(e.kind, e.name, e.path) for e in inv] == snapshot(name="inventory")
    for q in QUERIES:
        got = [(e.kind, e.name) for e in SK.rank(inv, q, limit=5)]
        assert got == snapshot(name=q)


API_MODES = {
    "ranked": {},
    "exact": {"exact": True},
    "regex": {"regex": True},
}
API_QUERIES = [
    "small functions",
    "plain tests names",
    "regression test",
    "plain|short",
    "functions",
]


def test_rule_search_rows_with_scores(project, snapshot):
    """The scores the CLI does not print: pinned through the API."""
    from ddflow.api import rules as RA
    from ddflow.services.rules import Rule

    for rid, title, content, tag in RULES:
        assert RA.rule_add(project, Rule(id=rid, title=title, content=content, tags=[tag])).ok
    got = {}
    for mode, kw in API_MODES.items():
        for q in API_QUERIES:
            out = RA.rule_search(project, q, **kw)
            got[f"{mode}:{q}"] = [(r["id"], r["score"]) for r in out.data.get("rows", [])]
    assert got == snapshot
