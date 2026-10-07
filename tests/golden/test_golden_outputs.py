"""Golden: what ddflow prints about a fixed project -- brief, recall, next, status, every
export and `config --explain` -- byte for byte, exit code included.

The project is `fixtures/project.jsonl` (see `project` in goldenfix.py): a
committed log with fixed timestamps, so nothing here depends on when the test runs.
"""

# ruff: noqa: F811 -- the goldenfix fixtures are imported, then named as parameters
from __future__ import annotations

import pytest
from goldenfix import _pinned_environment, ddflow, project  # noqa: F401 -- fixtures

OUTPUTS = {
    "brief": ["brief"],
    "brief --item T2": ["brief", "--item", "T2"],
    "brief --phase P1": ["brief", "--phase", "P1"],
    "recall parser": ["recall", "parser grammar"],
    "recall tokenizer bug": ["recall", "tokenizer comment"],
    "next": ["next"],
    "status": ["status"],
    "show T2": ["show", "T2"],
    "export list": ["export"],
    "config --explain": ["config", "--explain"],
}
EXPORTS = ["bugs", "changelog", "decisions", "roadmap", "rules", "sessions", "status", "worklog"]


@pytest.mark.parametrize("name", list(OUTPUTS))
def test_output(name, ddflow, snapshot):
    assert ddflow(*OUTPUTS[name]) == snapshot


@pytest.mark.parametrize("doc", EXPORTS)
def test_export(doc, ddflow, snapshot):
    assert ddflow("export", doc) == snapshot


def test_the_export_list_names_every_document_pinned_here(ddflow):
    """A new export document must be added to EXPORTS, or it ships unpinned. The listing is
    a table: a header row, one row per document (its name first), then a blank line."""
    _, listing = ddflow("export")
    lines = listing.splitlines()
    assert lines[0].split()[0] == "DOCUMENT"
    rows = [line.split()[0] for line in lines[1 : lines.index("")]]
    assert rows == EXPORTS
