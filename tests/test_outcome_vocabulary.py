"""One vocabulary for outcomes (B-uni-outcomes): what "finished" and "live" mean."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ddflow.core.model import ABANDONED, BLOCKED, DONE, OPEN, REVIEW, RUNNING, Item, State

PKG = Path(__file__).resolve().parents[1] / "ddflow"


@pytest.mark.parametrize(
    ("state", "terminal"),
    [
        (OPEN, False),
        (RUNNING, False),
        (BLOCKED, False),
        (REVIEW, False),
        (DONE, True),
        (ABANDONED, True),
    ],
)
def test_terminal_is_done_or_abandoned(state: str, terminal: bool) -> None:
    assert Item(id="x", kind="task", state=state).terminal is terminal


def _state() -> State:
    st = State()
    for n, removed in (("c", False), ("a", True), ("b", False)):
        st.items[n] = Item(id=n, kind="task", removed=removed)
    return st


def test_live_items_skips_removed_and_keeps_definition_order() -> None:
    st = _state()
    assert [i.id for i in st.live_items()] == ["c", "b"]
    assert list(st.live_by_id()) == ["c", "b"]
    assert st.live_by_id()["b"] is st.items["b"]


#: Sites that still spell "done or abandoned" out, awaiting their slice; only goes down.
OLD_TERMINAL_SPELLINGS = 11
OLD_LIVE_COMPREHENSIONS = 7


def _count(pattern: str) -> int:
    rx = re.compile(pattern)
    return sum(len(rx.findall(p.read_text())) for p in PKG.rglob("*.py"))


def test_terminal_spellings_only_go_down() -> None:
    n = _count(r"\(DONE, ABANDONED\)|\(ABANDONED, DONE\)")
    assert n <= OLD_TERMINAL_SPELLINGS, f"{n} sites; use Item.terminal"


def test_live_comprehensions_only_go_down() -> None:
    n = _count(r"for \w+ in \w+\.items\.values\(\) if not \w+\.removed")
    assert n <= OLD_LIVE_COMPREHENSIONS, f"{n} sites; use State.live_items()"
