"""The export, bisect, tests, precommit and ci commands, declared once
(B-uni-cmd-migrate.6i-export). The goldens pin every `--help` and `tools/list` byte; this pins
what the declarations carry that a generated parser or tool entry could lose.
"""

from __future__ import annotations

import pytest
from helpers import parse_cli as parse

from ddflow.api import bisect as A_BISECT
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.declared import export as X
from ddflow.surfaces.exemptions import FLAG_EXEMPT
from ddflow.surfaces.tools import TOOLS


def test_the_bisect_default_glob_is_the_apis():
    """The declaration cannot import the api, so it spells the glob; this holds them together."""
    assert X.BISECT_DEFAULT_GLOB == A_BISECT.DEFAULT_GLOB
    help_ = next(
        a.help for a in build_parser()._subparsers._group_actions[0].choices["bisect"]._actions
        if a.dest == "glob"
    )  # fmt: skip
    assert X.BISECT_DEFAULT_GLOB in help_


def test_each_tool_offers_what_it_always_did_in_the_order_it_served_it():
    assert list(TOOLS["ddflow_export"]["properties"]) == [
        "action", "mode", "doc", "all", "since", "version", "phase", "item", "status",
        "limit", "tag", "session", "max_bytes", "diff", "check", "write", "path",
    ]  # fmt: skip
    assert list(TOOLS["ddflow_bisect"]["properties"]) == ["victim", "cmd", "candidates", "timeout"]
    assert list(TOOLS["ddflow_ci"]["properties"]) == ["action", "ref", "base", "command"]
    assert list(TOOLS["ddflow_tests"]["properties"]) == ["item", "base"]
    assert list(TOOLS["ddflow_precommit"]["properties"]) == ["ddflow_cmd", "write"]


def test_the_flags_a_tool_omits_on_purpose_are_declared_with_their_reasons():
    for tool, flags in {
        "ddflow_export": {
            "--update",
            "--out",
            "--force",
            "--template",
            "--lock",
            "--local",
            "--yes",
        },
        "ddflow_bisect": {"--glob", "--repeat", "--max-runs"},
        "ddflow_ci": {"--stage", "--result", "--report", "--sha"},
    }.items():
        assert {f for (t, f) in FLAG_EXEMPT if t == tool} == flags
        assert all(FLAG_EXEMPT[(tool, f)] for f in flags)


def test_tests_and_precommit_are_told_where_the_caller_stands():
    assert (
        TOOLS["ddflow_tests"]["wants_called_from"]
        and TOOLS["ddflow_precommit"]["wants_called_from"]
    )
    assert "wants_called_from" not in TOOLS["ddflow_ci"]


def test_the_command_line_halves_keep_their_defaults_and_types():
    e = parse("export", "bugs", "--limit", "5", "--max-bytes", "100")
    assert (e.doc, e.target, e.limit, e.max_bytes, e.since, e.all) == (
        "bugs",
        "",
        5,
        100,
        "",
        False,
    )
    assert parse("export").max_bytes is None and parse("export").limit == 0
    b = parse("bisect", "V", "--cmd", "pytest {tests}", "--timeout", "2.5")
    assert (b.timeout, b.repeat, b.max_runs, b.glob) == (2.5, 1, 200, "")
    assert parse("ci").verb == "status" and parse("ci", "run").verb == "run"
    with pytest.raises(SystemExit):
        parse("ci", "bogus")
    with pytest.raises(SystemExit):
        parse("ci", "record", "--result", "maybe")
    assert parse("precommit").ddflow_cmd == "ddflow" and parse("precommit", "--write").write
    with pytest.raises(SystemExit):
        parse("bisect", "V")  # --cmd is required
