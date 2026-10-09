"""The cadence and pins commands declared once behave as they did typed out.

The goldens pin every `--help` and `tools/list` entry; these pin the defaults a handler
reads: `--min-needle` stays absent (None) so the API picks its own, `--top` is 10 and the
two cadence options are empty strings.
"""

from __future__ import annotations

import pytest

from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.declared import cadence
from ddflow.surfaces.tools import TOOLS


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def test_each_tool_is_served_by_its_declaration():
    assert [c.path for c in cadence.COMMANDS] == [("cadence",), ("pins",)]
    for command in cadence.COMMANDS:
        assert list(TOOLS[command.tool]["properties"]) == list(command.properties())


def test_cadence_options_default_to_empty_strings():
    ns = _parse("cadence")
    assert (ns.ran, ns.note) == ("", "")
    ns = _parse("cadence", "--ran", "integration", "--note", "n")
    assert (ns.ran, ns.note) == ("integration", "n")


def test_pins_keeps_the_defaults_its_handler_reads():
    ns = _parse("pins", "AGENTS.md")
    assert (ns.document, ns.tests, ns.min_needle, ns.top) == ("AGENTS.md", "", None, 10)
    ns = _parse("pins", "AGENTS.md", "--tests", "a,b", "--min-needle", "5", "--top", "0")
    assert (ns.tests, ns.min_needle, ns.top) == ("a,b", 5, 0)
    with pytest.raises(SystemExit):
        _parse("pins")  # the document is required
    with pytest.raises(SystemExit):
        _parse("pins", "AGENTS.md", "--top", "x")
    assert TOOLS["ddflow_pins"]["properties"]["document"][2] is True
