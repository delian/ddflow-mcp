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
    assert text.index("ddflow merge <id> --queue") - text.index("ddflow queue add") < 1500
