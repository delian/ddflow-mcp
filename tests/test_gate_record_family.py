"""One family_of (B-uni-gate-record.1-family): a model's family is `config.family_for`'s answer,
asked with the project's `[agent].families` or without them, and nowhere else."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ddflow import config
from ddflow.config import Config
from ddflow.services import gates as G
from ddflow.services import review as R


def test_the_old_review_name_is_the_one_implementation():
    assert R.family_of is config.family_for
    assert R.family_of("claude-sonnet-5") == "anthropic" and R.family_of("acme-1") == ""


def test_the_gate_view_is_the_same_question_with_the_projects_map():
    cfg = Config()
    cfg.agent.families = {"acme": "acme-labs"}
    assert G.family_of("acme-7b", cfg) == config.family_for("acme-7b", cfg.agent.families)
    assert G.family_of("acme-7b", cfg) == "acme-labs"
    assert R.family_of("acme-7b") == "", "the shipped map alone does not know it"
    assert G.family_of("claude-sonnet-5", Config()) == R.family_of("claude-sonnet-5")


def test_no_module_defines_a_family_rule_of_its_own():
    """`def family_of` exists once, as the cfg-taking view; the other name is an alias, and
    no module reads the shipped hints table itself."""
    root = Path(__file__).resolve().parents[1] / "ddflow"
    defs = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if re.search(r"^def family_of\(", p.read_text(), re.M)
    )
    assert defs == ["services/gates/reviewers.py"], defs
    readers = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p.name not in ("agent.py", "config.py")
        and any(
            (isinstance(n, ast.Name) and n.id == "FAMILY_HINTS")
            or (isinstance(n, ast.Attribute) and n.attr == "FAMILY_HINTS")
            for n in ast.walk(ast.parse(p.read_text()))
        )
    )
    assert readers == [], readers
