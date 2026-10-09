"""`recall`, `similar` and the lesson and decision searches are callers of the search core.

The output of each is pinned elsewhere (the recall goldens and `test_context_pack.py`); these
pin the seam: the core's index sources return what the store ranks, a source that fails does
not take the others down, and no API function asks a `Store` to search on its own.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api._base import _load
from ddflow.api.knowledge.lessons import _store
from ddflow.infra.store import RECALL_SOURCES
from ddflow.services import searchcore as SC

TABLES = {t for t, _, _ in RECALL_SOURCES}
ROOT = Path(__file__).resolve().parents[1] / "ddflow"


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(
        repo, "lesson", "add", "--id", "L-1", "--title", "retry with backoff",
        "--rule", "retry network calls with exponential backoff", "--new", agent="a",
    )  # fmt: skip
    run_cli(
        repo, "decision", "add", "--id", "D-1", "--title", "Retry policy",
        "--decision", "network retry uses backoff", "--new", agent="a",
    )  # fmt: skip
    run_cli(repo, "task", "add", "T-1", "--title", "retry the fetch", "--new", agent="a")
    return repo


def _store_of(repo):
    log, cfg, _ = _load(repo, "a")
    return _store(repo, log, cfg)


def test_the_core_returns_what_the_store_ranks_for_every_source(proj):
    store = _store_of(proj)
    found = SC.search_sources(store, "retry backoff", "", 3)
    assert set(found) >= {"decisions", "lessons", "items"}
    for table, rows in found.items():
        assert rows == store.search(table, "retry backoff", 3)
    assert list(found) == [t for t, _, _ in RECALL_SOURCES if t in found]  # reading order


def test_sources_are_chosen_by_table_or_by_label(proj):
    store = _store_of(proj)
    assert list(SC.search_sources(store, "retry", "lessons", 3)) == ["lessons"]
    assert list(SC.search_sources(store, "retry", "DECISION", 3)) == ["decisions"]
    assert SC.search_sources(store, "retry", "nosuch", 3) == {}


def test_a_source_named_in_any_case_is_selected(proj):
    """B89a8871f00: `--sources LESSONS` selected nothing while `lessons` and `lesson` did --
    the table name was compared case-sensitively and the label case-insensitively."""
    store = _store_of(proj)
    for spelled in ("lessons", "LESSONS", "Lessons", "lesson", "LESSON"):
        assert list(SC.search_sources(store, "retry", spelled, 3)) == ["lessons"], spelled
    assert SC.search_table(store, "lessons", "retry", 3) == store.search("lessons", "retry", 3)


def test_a_source_that_cannot_be_read_does_not_withhold_the_others(proj, monkeypatch):
    store = _store_of(proj)
    real = type(store).search

    def flaky(self, table, query, limit=5, **kw):
        if table == "lessons":
            raise RuntimeError("the lessons table is corrupt")
        return real(self, table, query, limit, **kw)

    monkeypatch.setattr(type(store), "search", flaky)
    found = SC.search_sources(store, "retry", "", 3)
    assert "lessons" not in found and "decisions" in found


def test_recall_and_the_searches_go_through_the_core(proj, monkeypatch):
    from ddflow.api import decisions, knowledge

    seen: list[str] = []
    for name in ("search_sources", "search_table", "matcher"):
        real = getattr(SC, name)

        def spy(*a, _real=real, _name=name, **kw):
            seen.append(_name)
            return _real(*a, **kw)

        monkeypatch.setattr(SC, name, spy)
    assert knowledge.recall(proj, "retry", agent="a").data["results"]
    assert knowledge.lesson_search(proj, "retry", agent="a").data["hits"]
    assert decisions.decision_search(proj, "retry").data["hits"]
    knowledge.similar(proj, "retry the network fetch", agent="a")
    assert seen == ["search_sources", "search_table", "search_table", "matcher"]


def test_the_matcher_refuses_a_missing_index_instead_of_answering_from_stale_weights(tmp_path):
    from ddflow.config import Config
    from ddflow.infra.store import Store

    store = Store(tmp_path, Config.load())
    with pytest.raises(LookupError):
        SC.matcher(store)


def _searching_calls(path: Path) -> list[int]:
    """The lines of ``path`` that call ``.search(...)`` on a store (a name or a call that
    says "store", or any receiver given a table name) or ``open_store(...)``, the duplicate check's matcher."""
    out = []
    for n in ast.walk(ast.parse(path.read_text())):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        first = (
            n.args[0] if n.args else next((k.value for k in n.keywords if k.arg == "table"), None)
        )
        names_a_table = isinstance(first, ast.Constant) and first.value in TABLES
        on_a_store = "store" in ast.unparse(n.func.value).lower()
        if (
            n.func.attr == "search" and (on_a_store or names_a_table)
        ) or n.func.attr == "open_store":
            out.append(n.lineno)
    return out


#: The only modules that may reach the index: the store, the core and the matcher itself.
HOMES = {"infra/store.py", "services/searchcore/indexed.py", "services/similar.py"}


def test_nothing_outside_the_core_asks_a_store_to_search_on_its_own():
    callers = {}
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in HOMES:
            continue
        if lines := _searching_calls(path):
            callers[rel] = lines
    assert callers == {}
