"""The words an agent is taught to recognise the skew guard by are the words the guard says
(B-uni-skew-doc): the refusal opens the same way whatever the install kind, and the driver,
the MCP instructions and the `[upgrade].skew` doc name that opener, not a remedy that only one
kind of install is given."""

from __future__ import annotations

from pathlib import Path

from ddflow.config import KNOB_DOCS
from ddflow.infra import log as LOG

ROOT = Path(__file__).resolve().parents[1]
OPENER = "REFUSED: this project's log has been worked on by ddflow"


def _flat(path: str) -> str:
    return " ".join((ROOT / path).read_text("utf-8").split())


def test_the_refusal_opens_the_same_way_for_every_install_kind(monkeypatch):
    remedy = {"installed": "Upgrade ddflow-mcp to >= 0.2.0", "source-tree": "Merge main into this"}
    for kind in ("installed", "source-tree"):
        monkeypatch.setattr(LOG, "install_kind", lambda kind=kind: kind)
        msg = LOG.skew_message("0.1.0", "0.2.0", "bob")
        assert msg.startswith(OPENER) and remedy[kind] in msg
        other = remedy["source-tree" if kind == "installed" else "installed"]
        assert other not in msg
        assert LOG.skew_message(
            "0.2.0",
            "0.2.0",
            "bob",
            log_format=3,
            format_level=2,
            format_only=True,
            format_version="0.2.0",
        ).startswith(OPENER)


def test_the_driver_and_the_instructions_recognise_the_guard_by_that_opener():
    for path in (
        "ddflow/templates/drivers/implement-phase.md",
        "docs/ddflow/drivers/implement-phase.md",
    ):
        text = _flat(path)
        assert f'begins "{OPENER} X"' in text, path
        assert "merge main" in text, path
    text = _flat("ddflow/templates/prompts/mcp_instructions.md")
    assert f'begins "{OPENER} X"' in text and "merge main" in text


def test_the_skew_knob_doc_says_what_a_source_checkout_is_told():
    doc = " ".join(KNOB_DOCS["upgrade.skew"].split())
    assert "Upgrade ddflow-mcp to >= X" in doc and "a source checkout is told to merge main" in doc


def test_the_rule_tool_descriptions_say_the_log_is_written():
    from ddflow.surfaces.mcp import TOOLS

    remove = TOOLS["ddflow_rule_remove"]["description"]
    assert "leaves no record" not in remove and "def.retired" in remove
    assert "def.updated" in TOOLS["ddflow_rule_edit"]["description"]
