"""`ddflow/surfaces/declared/records.py` was split into a package (r-small-files): every name the
module held still resolves from the old import path, the commands keep their exact order, and
every part is under the size limit."""

from __future__ import annotations

from pathlib import Path

import pytest

import ddflow.surfaces.declared.records as R

#: What the single module held at module level -- the names no caller may lose.
DEFINED = """
    ANSWER_FLAG_EXEMPT ANSWER_PARAMS BUG_FIXED_FIELDS BUG_INVALID_FIELDS BY_TOOL COMMAND CliPolicy
    Command MUTATION_FLAG_EXEMPT MUTATION_ORDER MUTATION_PARAMS OK O Param _answer _api _bug_dedupe_render
    _bug_reopen _bug_tag _regression_tests _reopening _tagging by_tool file_tasks_call importlib
    mutation_of
""".split()
DEFINED = [n for n in DEFINED if n != "COMMAND"] + ["COMMANDS"]

PATHS = [
    ("research",),
    ("bug", "found"),
    ("bug", "file-tasks"),
    ("bug", "dedupe"),
    ("bug", "duplicate"),
    ("bug", "fixed"),
    ("bug", "invalid"),
    ("bug", "tag"),
    ("bug", "reopen"),
    ("session", "start"),
    ("session", "prompt"),
    ("session", "message"),
    ("session", "inbox"),
    ("session", "note"),
    ("session", "adopt-orphans"),
    ("session", "end"),
]


@pytest.mark.parametrize("name", DEFINED)
def test_a_name_still_resolves_from_the_old_path(name):
    assert hasattr(R, name), name


def test_the_commands_keep_their_order_and_the_tool_table_is_built_from_them():
    assert [c.path for c in R.COMMANDS] == PATHS
    assert set(R.BY_TOOL) == {c.tool for c in R.COMMANDS if c.tool}


def test_the_old_module_file_is_gone_and_every_part_is_under_the_limit():
    root = Path(R.__file__).parent
    assert Path(R.__file__).name == "__init__.py"
    assert not (root.parent / "records.py").exists(), "the old module file is still there"
    for part in root.glob("*.py"):
        assert part.stat().st_size < 30000, part.name
