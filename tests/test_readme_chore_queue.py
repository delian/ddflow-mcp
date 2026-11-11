"""The docs name `ddflow chore` (commit, approve, the [chore] knobs) and `ddflow merge --queue`.

Both shipped (DX-chore-commit, DX-merge-queue-flag) with only a command-table row, so a reader
could not learn what a chore is, who approves the class, or that merge can enqueue.
"""

from __future__ import annotations

from readme_corpus import readme_corpus


def test_the_chore_class_is_explained():
    text = readme_corpus()
    for needle in (
        "ddflow chore commit",
        "ddflow chore approve",
        "[chore].globs",
        "[chore].skip_gates",
        "agent identity",
    ):
        assert needle in text, f"the docs never say {needle!r}"


def test_merge_queue_flag_sits_beside_the_queue_commands():
    text = readme_corpus()
    assert "ddflow merge <id> --queue" in text
    assert 0 < text.index("ddflow merge <id> --queue") - text.index("ddflow queue add") < 1500


def test_the_documented_default_skip_gates_are_the_configured_ones():
    from ddflow.config_sections.chore import ChoreConfig

    text = readme_corpus()
    start = text.index("`[chore].skip_gates` is the gates a chore skips")
    section = text[start : text.index("\n- ", start + 5)]
    for gate in ChoreConfig().skip_gates:
        assert gate in section, f"the default skipped gate {gate!r} is not in the documented list"
