"""The perf report prompt and help page name real commands, on both surfaces.

`test_help` holds help pages to one definition of "names a real command"; `test_remedy_texts`
holds printed advice and templates to the same. The perf report response is half of each:
a workflow command template an agent runs (`perf_report`), and a help page
(`docs/ddflow/perf-metrics.md`) a reader trusts. A page or prompt that points at a flag
that was renamed costs a reader more than none at all, so every `ddflow ...` / `ddflow_...`
it names must resolve in the argparse tree or the MCP tool table.

Found because the report's own footer already names the CLI and MCP next call, and the
prompt must quote it rather than invent a second spelling (r-tell-dont-discover).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_help import mentions, unknown_mentions

from ddflow.services import prompts as P

ROOT = Path(__file__).resolve().parents[1]


def test_the_perf_report_prompt_names_only_real_commands():
    text = P.resolve_command("perf_report").text
    unknown = unknown_mentions(mentions(text))
    assert not unknown, f"perf_report prompt names commands that do not exist: {unknown}"


def test_the_perf_help_page_names_only_real_commands():
    page = (ROOT / "docs" / "ddflow" / "perf-metrics.md").read_text("utf-8")
    unknown = unknown_mentions(mentions(page))
    assert not unknown, f"perf-metrics page names commands that do not exist: {unknown}"


def test_the_prompt_names_the_next_call_in_cli_and_mcp_form():
    """The conformance rule: every answer names the exact next call, CLI and MCP."""
    text = P.resolve_command("perf_report").text
    assert "ddflow perf report" in text, "the CLI form is not named"
    assert "ddflow_view action=perf" in text, "the MCP view-family form is not named"
    assert "ddflow_perf action=report" in text, "the MCP perf action is not named"


def test_the_help_page_names_the_wrapper_not_only_the_report(repo):
    """A reader asking 'how expensive is this command' must find the measuring wrapper,
    not only the report the log can already render."""
    page = (ROOT / "docs" / "ddflow" / "perf-metrics.md").read_text("utf-8")
    assert "ddflow perf run" in page
    assert "ddflow perf ingest" in page
    assert "ddflow perf env" in page


def test_the_prompt_rejects_and_supports_the_surfaces(repo):
    """The prompt one declaration surfaces (PM-report-surfaces): text, json, svg, html,
    a filter, a window and an out path."""
    text = P.resolve_command("perf_report").text
    for needle in ("--format", "--window", "--filters", "--out"):
        assert needle in text, needle
    for fmt in ("text", "json", "svg", "html"):
        assert fmt in text, fmt
