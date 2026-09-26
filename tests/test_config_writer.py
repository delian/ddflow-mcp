"""The config writer must not delete what it was not asked to change.

`_value_span` finds where a value ends, so `_toml_upsert` can replace a multi-line array
or string without orphaning its tail. It counted `[` and `{` without regard for quoting,
so an unbalanced opener inside a STRING VALUE started the depth at 1 and nothing ever
brought it back to 0 — a later `[section]` header contributes +1 then -1, i.e. net zero.
`_value_span` therefore returned `len(lines)`, and re-editing that key replaced
everything from it to END OF FILE.

Every downstream guard passed: the truncated text is valid TOML, so `Config.check` is
happy; the workflow diff is empty unless a deleted gate happened to be named by a
pipeline; `atomic_write` committed it and the command exited 0 reporting success. Silent
data loss in the one place that edits the operator's config.

Gate commands are arbitrary operator shell strings, so `sed 's/\\[//g'`, `cut -d'[' -f1`
and `grep -F '['` are all ordinary things to find in one.

*Found by the cross-family critic (DeepSeek on the LAN) on f90daaf, CONFIRMED, with a
trigger that reproduced first time.*
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.commands.config import _outside_quotes, _value_span

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

GATE = '\n[gate.lint]\ncommand = "sed \'s/\\\\[//g\'"\ntimeout_s = 900\ntitle = "Lint"\n'


def _cfg(repo: Path) -> dict:
    return tomllib.load((repo / ".ddflow" / "config.toml").open("rb"))


def test_an_unbalanced_bracket_in_a_value_does_not_truncate_the_file(repo):
    """The probe, as it was reported."""
    run_cli(repo, "init")
    path = repo / ".ddflow" / "config.toml"
    with path.open("a") as fh:
        fh.write(GATE)
    before = _cfg(repo)
    assert sorted(before["gate"]["lint"]) == ["command", "timeout_s", "title"]

    code, _out, err = run_cli(repo, "config", "--set", "gate.lint.command", "sed s/X//g")
    assert code == OK, err

    after = _cfg(repo)
    assert sorted(after["gate"]["lint"]) == ["command", "timeout_s", "title"], (
        f"sibling keys were deleted: {sorted(after['gate']['lint'])}"
    )
    for section in before:
        assert section in after, f"section {section!r} was deleted by an unrelated edit"


def test_a_real_bracketed_array_is_still_replaced_whole(repo):
    """The behaviour `_value_span` exists for, asserted so the fix cannot trade one bug
    for the other: a bracketed value must be replaced ENTIRELY, not have its tail
    orphaned as a stray `]` that TOML then rejects.

    Through `workflow pipeline`, which is what actually writes an array — `config --set`
    writes a string, and an earlier version of this test asserted against the wrong
    command and failed for a reason unrelated to its name.
    """
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "workflow", "pipeline", "task", "implement,unit_tests,merge")
    assert code == OK, err
    assert _cfg(repo)["gates"]["task_pipeline"] == ["implement", "unit_tests", "merge"]

    code, _out, err = run_cli(repo, "workflow", "pipeline", "task", "implement,merge")
    assert code == OK, err
    assert _cfg(repo)["gates"]["task_pipeline"] == ["implement", "merge"]
    # and the file is still parseable, i.e. no orphaned bracket
    assert _cfg(repo)["gates"]


def test_outside_quotes_ignores_brackets_in_strings_and_keeps_real_ones():
    assert "[" not in _outside_quotes('command = "sed [x]"')
    assert "[" not in _outside_quotes("cmd = 'a[b'")  # literal string, no escapes
    assert "[" in _outside_quotes("task_pipeline = [")  # a REAL opener survives
    assert "[" not in _outside_quotes("x = 1  # [note]")  # comment


def test_a_comment_marker_inside_a_string_is_not_treated_as_a_comment():
    """The hazard in the other direction. Splitting on `#` before scanning quotes would
    truncate a value containing one, so the scan has to decide both at once."""
    assert _outside_quotes('c = "a#b["').count("[") == 0
    assert _outside_quotes("x = [  # trailing note").count("[") == 1


def test_the_span_of_a_value_with_a_quoted_bracket_is_one_line():
    lines = [('command = "sed [x]"', False), ("timeout_s = 900", False), ('title = "L"', False)]
    assert _value_span(lines, 0) == 1, "the span ran past the key it was asked about"
