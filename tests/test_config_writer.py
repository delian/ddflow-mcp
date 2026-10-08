"""The config writer must not delete what it was not asked to change.

`_toml_upsert` (tomlkit, since B-uni-fsio-toml) must replace a multi-line array or string
without orphaning its tail. The line editor it replaced counted `[` and `{` without regard
for quoting, so an unbalanced opener inside a STRING VALUE started the depth at 1 and
nothing ever brought it back to 0. Its span of the value ran to the end of the file, and
re-editing that key replaced everything from it to END OF FILE.

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

from ddflow.infra.tomlcfg import upsert as _toml_upsert

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
    """The behaviour the value-span logic existed for, asserted so the fix cannot trade one bug
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


BRACKETED = (
    "[gate.lint]\n"
    "command = \"sed 's/\\\\[//g' # [x]\"  # keep this note\n"
    "timeout_s = 900\n"
    "steps = [\n"
    '  "a",  # first\n'
    '  "b[",\n'
    "]\n"
    'title = "Lint"\n'
)


def test_a_bracket_or_comment_marker_inside_a_string_is_only_text():
    """Neither the `[` nor the `#` in the value changes where the edit lands, and every
    sibling key, array element and comment is still there."""
    out = _toml_upsert(BRACKETED, "gate.lint.timeout_s", "5")
    data = tomllib.loads(out)["gate"]["lint"]
    assert data["timeout_s"] == 5
    assert data["command"] == "sed 's/\\[//g' # [x]"
    assert data["steps"] == ["a", "b["] and data["title"] == "Lint"
    assert "# keep this note" in out and "# first" in out


def test_a_multi_line_array_is_replaced_whole_and_comments_elsewhere_survive():
    out = _toml_upsert(BRACKETED, "gate.lint.steps", '["z"]')
    assert tomllib.loads(out)["gate"]["lint"]["steps"] == ["z"]
    assert "# keep this note" in out and "b[" not in out


def test_a_header_with_a_trailing_comment_is_the_same_section():
    out = _toml_upsert("[gates]  # how work is checked\nx = 1\n", "gates.y", "2")
    assert out.count("[gates]") == 1 and tomllib.loads(out)["gates"] == {"x": 1, "y": 2}


def test_a_new_section_is_appended_after_a_blank_line():
    assert _toml_upsert("a = 1\n", "s.k", '"v"') == 'a = 1\n\n[s]\nk = "v"\n'
    assert _toml_upsert("", "s.k", "true") == "[s]\nk = true\n"


def test_text_that_is_not_toml_is_reported_as_toml_does():
    import pytest

    with pytest.raises(tomllib.TOMLDecodeError):
        _toml_upsert("[a\nx = 1\n", "a.y", "1")
