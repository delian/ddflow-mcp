"""Golden: what ddflow prints about a fixed project -- brief, recall, next, status, every
export and `config --explain` -- byte for byte, exit code included.

The project is `fixtures/project.jsonl` (see `project` in this directory's conftest): a
committed log with fixed timestamps, so nothing here depends on when the test runs.
"""

from __future__ import annotations

import pytest

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
    """A new export document must be added to EXPORTS, or it ships unpinned."""
    _, listing = ddflow("export")
    rows = [line.split()[0] for line in listing.splitlines()[1:] if line and not line[0].isspace()]
    assert [r for r in rows if r.islower() and r.isalpha()] == EXPORTS
