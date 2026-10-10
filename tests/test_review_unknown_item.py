"""B8d9e8108f8: `ddflow review <unknown id>` recorded the review gate on an item that does
not exist, and the fold created a phantom task."""

from __future__ import annotations

from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _items(repo):
    return fold(EventLog(repo, "reader").read_all(), strict=False).items


def test_reviewing_an_unknown_item_is_refused_and_records_nothing(repo) -> None:
    before = set(_items(repo))
    code, out, err = run_cli(repo, "review", "NO-SUCH-ITEM", "--gate", "critic")
    assert code != 0, (out, err)
    assert "no such item" in (out + err), (out, err)
    assert set(_items(repo)) == before
    assert "NO-SUCH-ITEM" not in _items(repo)


def test_reviewing_several_gates_of_an_unknown_item_is_refused_too(repo) -> None:
    code, out, err = run_cli(repo, "review", "NO-SUCH-ITEM", "--gate", "rubber_duck,critic")
    assert code != 0 and "no such item" in (out + err), (out, err)
    assert "NO-SUCH-ITEM" not in _items(repo)
