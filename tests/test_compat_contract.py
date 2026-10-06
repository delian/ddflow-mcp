"""The compatibility contract (docs/ddflow/compatibility.md, D-compat) and the code agree."""

from __future__ import annotations

import re
from pathlib import Path

import ddflow

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ddflow" / "compatibility.md"

#: The interface kinds the contract must cover (D-compat; B-uni-compat-contract's body).
INTERFACES = (
    "CLI commands and flags",
    "MCP tools",
    "`--json` output",
    "Config keys and values",
    "Event kinds and payloads",
    "Export template data",
    "Prompt template variables",
    "Hook commands",
    "On-disk formats",
)


def _doc() -> str:
    return DOC.read_text("utf-8")


def test_every_interface_kind_is_listed():
    rows = {m.group(1) for m in re.finditer(r"^\| (.+?) \|", _doc(), re.M)}
    missing = [i for i in INTERFACES if i not in rows]
    assert not missing, f"interfaces the contract does not list: {missing}"


def test_the_three_change_classes_are_defined():
    for cls in ("**Additive.**", "**Deprecating.**", "**Breaking.**"):
        assert cls in _doc(), cls


def test_format_level_is_a_plain_positive_integer_the_doc_states():
    assert isinstance(ddflow.FORMAT_LEVEL, int) and not isinstance(ddflow.FORMAT_LEVEL, bool)
    assert ddflow.FORMAT_LEVEL >= 1
    stated = re.search(r"\(currently \*\*(\d+)\*\*\)", _doc())
    assert stated and int(stated.group(1)) == ddflow.FORMAT_LEVEL, (
        "docs/ddflow/compatibility.md states a different FORMAT_LEVEL than ddflow/__init__.py"
    )
    # a plain literal, like __version__, so tools can read it as text
    init = (ROOT / "ddflow" / "__init__.py").read_text("utf-8")
    assert re.search(r"^FORMAT_LEVEL = \d+$", init, re.M)


def test_the_readme_points_to_the_contract():
    assert "(docs/ddflow/compatibility.md)" in (ROOT / "README.md").read_text("utf-8")


#: Where each part of the contract is enforced (the doc's Enforcement table).
ENFORCED_BY = (
    "tests/test_compat_contract.py",
    "B-uni-compat-aliases",
    "B-uni-compat-config",
    "B-uni-compat-events",
    "B-uni-compat-artifacts",
    "B-uni-compat-json",
    "B-uni-compat-tests",
    "B-uni-compat-release-gate",
)


def test_the_enforcement_table_names_every_check():
    table = _doc().split("## Enforcement", 1)[1]
    missing = [t for t in ENFORCED_BY if t not in table]
    assert not missing, f"the Enforcement table no longer names: {missing}"


def test_every_change_class_has_a_version_bump():
    numbering = _doc().split("## Version numbers", 1)[1].split("##", 1)[0]
    for cls in ("additive", "deprecating", "breaking"):
        assert cls in numbering, f"no version bump stated for {cls} changes"
