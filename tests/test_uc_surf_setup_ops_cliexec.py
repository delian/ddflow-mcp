"""The executor's duplicate-check test reads ``candidates`` only when it is a list: bisect's
``candidates`` is a count, and its exit-2 report must reach stdout (B-uc-surf-setup-ops)."""

from __future__ import annotations

from types import SimpleNamespace

from ddflow.surfaces.cliexec import _asked_duplicates


def _out(exit_code, candidates):
    return SimpleNamespace(exit=exit_code, data={"candidates": candidates})


def test_a_count_is_not_a_duplicate_answer():
    assert not _asked_duplicates(_out(2, 5))


def test_a_list_on_a_non_zero_exit_is_one():
    assert _asked_duplicates(_out(3, [{"id": "T1"}]))


def test_an_empty_list_or_a_zero_exit_is_not():
    assert not _asked_duplicates(_out(3, []))
    assert not _asked_duplicates(_out(0, [{"id": "T1"}]))
