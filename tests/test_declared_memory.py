"""The job and memory commands declared once behave as they did typed out.

The goldens pin every `--help` and `tools/list` entry; these pin the shapes the migration
had to carry: the defaults a handler reads (`""` and not `None`, except an exit code that
may be absent), the duplicate-check answer flags on `memory add` and the group dispatch.
"""

from __future__ import annotations

import pytest
from helpers import parse_cli as _parse

from ddflow.surfaces.declared import memory
from ddflow.surfaces.tools import TOOLS


def test_every_job_and_memory_tool_is_served_by_its_declaration():
    assert [c.path for c in memory.COMMANDS] == [
        ("job", "run"), ("job", "add"), ("job", "list"), ("job", "end"),
        ("memory", "add"), ("memory", "list"), ("memory", "forget"),
    ]  # fmt: skip
    for command in memory.COMMANDS:
        assert list(TOOLS[command.tool]["properties"]) == list(command.properties())


def test_a_job_leaf_reaches_the_handler_with_the_defaults_it_reads():
    run = _parse("job", "run", "T1", "sleep 1")
    assert (run.item, run.command, run.log, run.cwd, run.job_cmd) == (
        "T1",
        "sleep 1",
        "",
        "",
        "run",
    )
    add = _parse("job", "add", "T1", "--pid", "5")
    assert (add.pid, add.command, add.log) == (5, "", "")
    end = _parse("job", "end", "J1")
    assert (end.job, end.exit_code, end.note, end.force) == ("J1", None, "", False)
    assert _parse("job", "end", "J1", "--exit-code", "3").exit_code == 3
    ls = _parse("job", "list")
    assert (ls.item, ls.all) == ("", False)
    with pytest.raises(SystemExit):
        _parse("job", "add", "T1")  # --pid is required
    with pytest.raises(SystemExit):
        _parse("job", "end", "J1", "--exit-code", "x")


def test_memory_add_keeps_the_duplicate_check_answer_flags():
    ns = _parse("memory", "add", "fact", "--tags", "a,b", "--id", "M1", "--extends", "M2")
    assert (ns.text, ns.tags, ns.id, ns.extends, ns.memory_cmd) == (
        "fact",
        "a,b",
        "M1",
        "M2",
        "add",
    )
    assert _parse("memory", "add", "fact", "--new").new is True
    assert _parse("memory", "add", "fact", "--check").check is True
    with pytest.raises(SystemExit):
        _parse("memory", "add", "fact", "--new", "--related", "M1")  # at most one answer
    props = TOOLS["ddflow_memory_add"]["properties"]
    assert list(props) == ["text", "tags", "id", "relation", "check_only"]


def test_memory_list_and_forget_take_what_they_took():
    ls = _parse("memory", "list", "--query", "q", "--limit", "3", "--all")
    assert (ls.query, ls.limit, ls.all) == ("q", 3, True)
    assert (_parse("memory", "list").query, _parse("memory", "list").limit) == ("", 0)
    assert _parse("memory", "forget", "M1", "--reason", "r").reason == "r"
    with pytest.raises(SystemExit):
        _parse("memory", "forget", "M1")  # --reason is required
