"""Onboarding stages 4-5: cutover proposals from the rulebook, and the freeze ratchet.

Stage 4 exists because a rulebook line that survives the import is worse than an unused
file: the agent follows the rule, edits a file nothing reads, and the ddflow record
silently disagrees. Stage 5 exists because an edit to an imported file after the cutover
reaches no agent and forks the record -- a ticked box that leaves the item open.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import legacy as L
from ddflow.services.adopt import Refused
from ddflow.services.enforce import read_precommit_yaml


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- stage 4: the rulebook scan -------------------------------------------------------


def test_a_duty_line_proposes_the_ddflow_replacement(repo):
    _write(
        repo,
        "CLAUDE.md",
        "# Rules\n- When done, tick the checkbox.\n- Append to the journal.\n",
    )
    proposals = L.scan(repo, [])
    assert [p.line for p in proposals] == [2, 3]
    assert "ddflow_complete" in proposals[0].replacement
    assert "ddflow_session_note" in proposals[1].replacement
    assert proposals[0].text == "- When done, tick the checkbox."


def test_an_imported_filename_gets_its_own_proposal(repo):
    _write(repo, "AGENTS.md", "- The backlog lives in todo.md.\n")
    proposals = L.scan(repo, ["todo.md"])
    assert len(proposals) == 1
    assert "imported into ddflow" in proposals[0].replacement
    assert "frozen" in proposals[0].replacement


def test_the_managed_block_is_never_scanned(repo):
    """ddflow regenerates that block and its text already speaks ddflow; editing the
    operator's copy of it is never the cutover."""
    _write(
        repo,
        "CLAUDE.md",
        f"intro\n{L.BEGIN}\n- tick the checkbox\n{L.END}\n- tick the checkbox\n",
    )
    proposals = L.scan(repo, [])
    assert [p.line for p in proposals] == [5]


def test_a_line_that_already_speaks_ddflow_is_left_alone(repo):
    _write(repo, "CLAUDE.md", "- tick the checkbox (see `ddflow_complete`)\n")
    assert L.scan(repo, []) == []


def test_slash_commands_and_named_handoff_docs_are_scanned(repo):
    _write(repo, ".claude/commands/done.md", "Tick the box when finished.\n")
    _write(repo, "docs/HANDOFF.md", "Remember to update lessons.md each time.\n")
    proposals = L.scan(repo, [], extra=["docs/HANDOFF.md"])
    assert sorted(p.path for p in proposals) == [".claude/commands/done.md", "docs/HANDOFF.md"]
    assert "ddflow_lesson_add" in proposals[1].replacement


def test_a_basename_does_not_match_inside_a_longer_name(repo):
    _write(repo, "CLAUDE.md", "- Read BACKLOG.md before starting.\n")
    assert L.scan(repo, ["LOG.md"]) == []


def test_no_candidates_renders_a_clear_report():
    assert "no cutover proposals" in L.render([])


def test_render_shows_path_line_text_and_replacement(repo):
    _write(repo, "CLAUDE.md", "- tick the checkbox\n")
    out = L.render(L.scan(repo, []))
    assert "CLAUDE.md:1: - tick the checkbox" in out
    assert "-> claim the item" in out


# --- the imported-file set ------------------------------------------------------------


def test_imported_files_are_the_file_part_of_every_origin():
    state = SimpleNamespace(
        items={"i": SimpleNamespace(source="docs/todo.md:41")},
        memories={"m": SimpleNamespace(source="notes.md:2")},
        research={"r": SimpleNamespace(sources=["research.md:9", "git:branch/x"])},
    )
    assert L.imported_files(state) == ["docs/todo.md", "notes.md", "research.md"]


def test_typed_records_and_branches_are_not_files():
    assert L.files_from_sources(["", "git:feature/x", "same.md:1", "same.md:9"]) == ["same.md"]


# --- stage 5: the manifest and the generated test -------------------------------------


def test_freeze_without_precommit_writes_manifest_and_a_failing_test(repo):
    _write(repo, "todo.md", "the old backlog\n")
    actions = L.freeze(repo, ["todo.md"])
    assert L.read_frozen(repo) == {"todo.md": L.sha256_file(repo / "todo.md")}
    assert L.check_frozen(repo) == []
    assert (repo / L.MANIFEST_REL).is_file()
    generated = repo / L.GENERATED_TEST_ROOT  # no tests/ directory in this repo
    assert generated.is_file() and L.TEST_MARK in generated.read_text()
    assert not (repo / L.GENERATED_TEST).exists()
    assert any("suite goes red" in a for a in actions)


def test_the_generated_test_goes_red_when_a_byte_changes(repo):
    """The ratchet itself gets mutation-checked: run the generated test, change one
    byte, run it again and watch it fail."""
    _write(repo, "todo.md", "the old backlog\n")
    L.freeze(repo, ["todo.md"])
    generated = repo / L.GENERATED_TEST_ROOT
    namespace = {"__file__": str(generated)}
    exec(compile(generated.read_text("utf-8"), str(generated), "exec"), namespace)
    namespace["test_frozen_imports_are_unchanged"]()
    (repo / "todo.md").write_text("the old backlog\nand one more line\n")
    with pytest.raises(AssertionError, match=r"todo\.md"):
        namespace["test_frozen_imports_are_unchanged"]()


def test_the_generated_test_lands_in_tests_when_that_is_where_tests_live(repo):
    (repo / "tests").mkdir()
    _write(repo, "todo.md", "x\n")
    L.freeze(repo, ["todo.md"])
    generated = repo / L.GENERATED_TEST
    assert generated.is_file()
    namespace = {"__file__": str(generated)}
    exec(compile(generated.read_text("utf-8"), str(generated), "exec"), namespace)
    namespace["test_frozen_imports_are_unchanged"]()


def test_check_frozen_reports_a_changed_file_and_a_missing_one(repo):
    _write(repo, "a.md", "a\n")
    _write(repo, "b.md", "b\n")
    L.freeze(repo, ["a.md", "b.md"])
    assert L.check_frozen(repo) == []
    (repo / "a.md").write_text("a changed\n")
    (repo / "b.md").unlink()
    assert L.check_frozen(repo) == ["a.md", "b.md"]


def test_no_manifest_is_not_a_pass(repo):
    assert L.read_frozen(repo) is None
    assert L.check_frozen(repo) is None, "'never frozen' is not 'everything unchanged'"


def test_a_manifest_that_validly_freezes_nothing_is_present_not_absent(repo):
    """The operator may unfreeze the last file -- the manifest's own comment says so;
    the ratchet is then present and watches nothing, which is not the same as missing."""
    _write(repo, L.MANIFEST_REL, "# all unfrozen by the operator\n[frozen]\n")
    assert L.read_frozen(repo) == {}
    assert L.check_frozen(repo) == []


def test_a_malformed_manifest_is_not_nothing_frozen(repo):
    _write(repo, L.MANIFEST_REL, "[frozen]\nx = 1\n")
    with pytest.raises(ValueError, match="frozen"):
        L.read_frozen(repo)
    with pytest.raises(ValueError):
        L.check_frozen(repo)


def test_missing_imported_files_are_reported_not_invented(repo):
    _write(repo, "here.md", "x\n")
    actions = L.freeze(repo, ["gone.md", "here.md"])
    assert any("gone.md" in a and "no longer exists" in a for a in actions)
    assert L.read_frozen(repo) == {"here.md": L.sha256_file(repo / "here.md")}


def test_no_files_at_all_writes_no_ratchet(repo):
    actions = L.freeze(repo, ["gone.md"])
    assert any("no imported files to freeze" in a for a in actions)
    assert not (repo / L.MANIFEST_REL).exists()
    assert not (repo / L.GENERATED_TEST_ROOT).exists()


# --- stage 5: the pre-commit variant --------------------------------------------------


PRE_COMMIT = """repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff
"""


def test_a_precommit_project_gets_a_fail_hook_instead_of_a_test(repo):
    _write(repo, "todo.md", "x\n")
    _write(repo, ".pre-commit-config.yaml", PRE_COMMIT)
    actions = L.freeze(repo, ["todo.md"])
    text = (repo / ".pre-commit-config.yaml").read_text()
    assert L.HOOK_ID in text and "language: fail" in text
    assert "'^(?:todo\\.md)$'" in text
    assert isinstance(read_precommit_yaml(text), dict)
    assert not (repo / L.GENERATED_TEST_ROOT).exists()
    assert any("frozen-files hook" in a for a in actions)
    second = L.freeze(repo, ["todo.md"])
    assert any("updated the frozen-files hook in .pre-commit-config.yaml" in a for a in second)
    assert (repo / ".pre-commit-config.yaml").read_text() == text


def test_the_hook_lines_up_with_a_column_zero_repos_list(repo):
    _write(repo, "todo.md", "x\n")
    _write(
        repo,
        ".pre-commit-config.yaml",
        "repos:\n- repo: https://example/x\n  hooks:\n    - id: y\n",
    )
    L.freeze(repo, ["todo.md"])
    text = (repo / ".pre-commit-config.yaml").read_text()
    assert "\n- repo: local\n" in text, "a column-zero list must get a column-zero item"
    assert isinstance(read_precommit_yaml(text), dict)


def test_the_hook_is_replaced_when_a_different_file_is_frozen_later(repo):
    _write(repo, "a.md", "a\n")
    _write(repo, "b.md", "b\n")
    _write(repo, ".pre-commit-config.yaml", PRE_COMMIT)
    L.freeze(repo, ["a.md"])
    L.freeze(repo, ["b.md"])
    text = (repo / ".pre-commit-config.yaml").read_text()
    assert "b\\.md" in text and "a\\.md" not in text
    assert text.count(L.HOOK_BEGIN) == 1
    assert isinstance(read_precommit_yaml(text), dict)


def test_a_test_file_that_is_not_ddflows_is_never_overwritten(repo):
    _write(repo, "a.md", "a\n")
    _write(repo, L.GENERATED_TEST_ROOT, "def test_mine():\n    assert True\n")
    actions = L.freeze(repo, ["a.md"])
    refused = [a for a in actions if isinstance(a, Refused)]
    assert refused and "not ddflow's" in refused[0]
    assert (repo / L.GENERATED_TEST_ROOT).read_text() == "def test_mine():\n    assert True\n"
    assert not (repo / L.MANIFEST_REL).exists(), "a refusal must not leave a ratchet-less manifest"


def test_an_incomplete_hook_block_is_refused_not_duplicated(repo):
    """A half-deleted managed block must not be 'completed' by inserting a second one:
    the next run would replace the span from the orphan marker to the new end, deleting
    whatever the operator has between them (rubber_duck on 276c2cbe)."""
    _write(repo, "a.md", "a\n")
    orphan_begin = f"repos:\n\n{L.HOOK_BEGIN}\n- repo: local\n"
    _write(repo, ".pre-commit-config.yaml", orphan_begin)
    actions = L.freeze(repo, ["a.md"])
    refused = [a for a in actions if isinstance(a, Refused)]
    assert refused and "clean the block up by hand" in refused[0]
    assert (repo / ".pre-commit-config.yaml").read_text() == orphan_begin
    assert not (repo / L.MANIFEST_REL).exists()


def test_two_managed_blocks_are_refused(repo):
    _write(repo, "a.md", "a\n")
    block = f"{L.HOOK_BEGIN}\n- repo: local\n  hooks:\n    - id: x\n{L.HOOK_END}\n"
    text = f"repos:\n{block}{block}"
    _write(repo, ".pre-commit-config.yaml", text)
    actions = L.freeze(repo, ["a.md"])
    refused = [a for a in actions if isinstance(a, Refused)]
    assert refused and "2 begin and 2 end" in refused[0]
    assert (repo / ".pre-commit-config.yaml").read_text() == text


def test_an_end_marker_before_its_begin_marker_is_refused(repo):
    _write(repo, "a.md", "a\n")
    text = f"repos:\n{L.HOOK_END}\n# later\n{L.HOOK_BEGIN}\n"
    _write(repo, ".pre-commit-config.yaml", text)
    actions = L.freeze(repo, ["a.md"])
    refused = [a for a in actions if isinstance(a, Refused)]
    assert refused and "before its begin marker" in refused[0]
    assert (repo / ".pre-commit-config.yaml").read_text() == text


def test_a_config_that_would_not_parse_is_refused_untouched(repo):
    _write(repo, "a.md", "a\n")
    broken = "repos:\n\t- repo: https://example/x\n"
    _write(repo, ".pre-commit-config.yaml", broken)
    actions = L.freeze(repo, ["a.md"])
    refused = [a for a in actions if isinstance(a, Refused)]
    assert refused and "would not parse" in refused[0]
    assert (repo / ".pre-commit-config.yaml").read_text() == broken
