"""P-extension-seams.registry: one typed `Registry[T]` for the in-tree seams.

The migrated seams (export kinds, ticks, search sources, item kinds, harness descriptors)
must list exactly the names they listed before, and the hand-rolled `register()` idioms that
remain may only shrink.
"""

from __future__ import annotations

import ast
import re
import sys
import types
from pathlib import Path

import pytest

from ddflow.core.registry import DuplicateName, Lazy, Registry, RegistryError, UnknownName

ROOT = Path(__file__).resolve().parent.parent


# -- the helper ------------------------------------------------------------------------


def _reg(**kw) -> Registry[str]:
    return Registry("widget", key=lambda s: s.split(":")[0], **kw)


def test_names_are_sorted_or_in_registration_order():
    a, b = _reg(), _reg(order="registered")
    for r in (a, b):
        for item in ("zed:1", "alpha:2", "mid:3"):
            r.register(item)
    assert a.names() == ["alpha", "mid", "zed"] and list(a) == ["alpha", "mid", "zed"]
    assert b.names() == ["zed", "alpha", "mid"]
    assert b.values_list() == ["zed:1", "alpha:2", "mid:3"]
    with pytest.raises(ValueError, match="sorted"):
        _reg(order="newest")


def test_a_taken_name_is_an_error_unless_the_same_definition_or_replace_is_allowed():
    r = _reg()
    r.register("a:1")
    with pytest.raises(DuplicateName, match=r"widget .a. is already registered$"):
        r.register("a:1")
    same = _reg(same_ok=True)
    same.register("a:1")
    same.register("a:1")  # the same definition again: a no-op
    with pytest.raises(DuplicateName, match="with another definition"):
        same.register("a:2")
    rep = _reg(replace=True, order="registered")
    rep.register("a:1")
    rep.register("b:1")
    rep.register("a:2")
    assert rep.values_list() == ["a:2", "b:1"]
    with pytest.raises(ValueError, match="two answers"):
        _reg(same_ok=True, replace=True)


def test_an_unknown_name_lists_those_known_and_is_a_keyerror_for_mapping_get():
    r = _reg()
    r.register("a:1")
    r.register("b:1")
    with pytest.raises(UnknownName, match=r"unknown widget 'c'\. Known: a, b"):
        r.get_or_raise("c")
    assert r.get("c") is None and r.get("a") == "a:1" and "a" in r and "c" not in r
    assert isinstance(UnknownName("x"), KeyError) and isinstance(UnknownName("x"), RegistryError)
    with pytest.raises(UnknownName, match=r"Known: \(none\)"):
        _reg().get_or_raise("x")


def test_a_name_is_given_when_there_is_no_key():
    r: Registry[int] = Registry("number")
    with pytest.raises(RegistryError, match="give a name"):
        r.register(1)
    assert r.register(1, "one") == 1 and r["one"] == 1


def test_a_lazy_target_imports_when_first_used_and_the_listing_says_so():
    r: Registry[object] = Registry("fn")
    r.lazy("join", "os.path:join")
    assert r.listing() == [{"name": "join", "module": "os.path", "lazy": True}]
    import os.path

    assert r["join"] is os.path.join
    assert r.listing()[0]["lazy"] is False
    with pytest.raises(DuplicateName):
        r.lazy("join", "os.path:split")
    with pytest.raises(RegistryError, match=r"package.module:attr"):
        Lazy("nonsense").resolve()


def test_a_loader_fills_the_registry_once_and_a_failure_is_not_remembered():
    calls = []

    def load():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("descriptor broken")
        return ["b:1", "a:1"]

    r = _reg(loader=load, order="registered")
    with pytest.raises(RuntimeError):
        r.names()
    assert r.names() == ["b", "a"] and r.names() == ["b", "a"] and len(calls) == 2


def test_a_loader_that_fails_midway_registers_nothing_and_the_error_is_the_real_one():
    calls = []

    def load():
        calls.append(1)
        yield "a:1"
        if len(calls) == 1:
            raise RuntimeError("bad descriptor")
        yield "b:1"

    r = _reg(loader=load, order="registered")
    for _ in range(1):
        with pytest.raises(RuntimeError, match="bad descriptor"):
            r.names()
    assert r.table == {}
    assert r.names() == ["a", "b"]


def test_removing_a_name_from_a_loaded_registry_sticks():
    r = _reg(loader=lambda: ["a:1", "b:1"])
    r.unregister("a")  # before the first read: the registry is filled first
    assert r.names() == ["b"]
    with pytest.raises(UnknownName):
        r.get_or_raise("a")


def test_discovery_imports_every_prefixed_module_once(tmp_path, monkeypatch):
    pkg = tmp_path / "seam_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "kind_one.py").write_text("from seam_reg import REG\nREG.register('one:1')\n")
    (pkg / "kind_two.py").write_text("from seam_reg import REG\nREG.register('two:2')\n")
    (pkg / "helper.py").write_text("raise RuntimeError('not a kind module')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    mod = types.ModuleType("seam_reg")
    mod.REG = _reg(package="seam_pkg", prefix="kind_")
    monkeypatch.setitem(sys.modules, "seam_reg", mod)
    try:
        assert mod.REG.names() == ["one", "two"]
        mod.REG.discover()  # once: a second pass would be a duplicate-name error
        assert len(mod.REG) == 2
    finally:
        for name in ("seam_pkg", "seam_pkg.kind_one", "seam_pkg.kind_two"):
            sys.modules.pop(name, None)


# -- the migrated seams list what they listed before -------------------------------------

EXPORT_KINDS = [
    "bugs",
    "changelog",
    "decisions",
    "roadmap",
    "rules",
    "sessions",
    "status",
    "worklog",
]
TICKS = ["flow.sample"]
SEARCH_SOURCES = [
    "records",
    "sessions",
    "prompts",
    "log",
    "rules",
    "skills",
    "agents",
    "jobs",
    "schedules",
]
ITEM_KINDS = ["task", "phase", "bug", "doc", "research", "job"]
HARNESSES = [
    "claude", "gemini", "codex", "copilot", "vscode", "kilo", "cursor", "kimi", "opencode",
    "glm", "qwen", "antigravity", "devin", "qodo", "tabnine", "aider", "cline", "windsurf",
    "replit", "openhands", "goose", "cody",
]  # fmt: skip


def test_the_export_kinds_list_the_same_names():
    from ddflow.services.export import registry as R

    assert R.names() == EXPORT_KINDS and isinstance(R._REG, Registry)
    assert R.get("status").name == "status"
    with pytest.raises(R.ExportError, match=r"unknown document kind 'nope'\. Known: bugs, "):
        R.get("nope")
    with pytest.raises(R.ExportError, match="replay is not an export kind"):
        R.get("replay")
    with pytest.raises(ValueError, match="export kind 'status' is already registered"):
        R.register(R.get("status"))


def test_the_ticks_list_the_same_names_and_keep_their_duplicate_rule():
    import ddflow.api  # noqa: F401 -- registers the shipped ticks
    from ddflow.services import ticks as T

    assert [t.name for t in T.registered()] == TICKS and isinstance(T.REGISTRY, Registry)
    tick = T.REGISTRY["flow.sample"]
    T.register(tick)  # the same definition again
    other = T.Tick("flow.sample", tick.every_s + 1, tick.budget_s, tick.target)
    with pytest.raises(ValueError, match="already registered with another definition"):
        T.register(other)


def test_the_ticks_are_listed_sorted_by_name_whatever_the_registration_order():
    from ddflow.services import ticks as T

    noop = "os.path:join"
    names = ["zz.b-test", "zz.a-test", "zz.c-test"]
    try:
        for n in names:
            T.register(T.Tick(n, 60, 1, noop))
        listed = [t.name for t in T.registered() if t.name.startswith("zz.")]
        assert listed == sorted(names)
    finally:
        for n in names:
            T.unregister(n)


def test_the_search_sources_list_the_same_names_in_the_same_order():
    from ddflow.services import search  # noqa: F401 -- registers the sources
    from ddflow.services.searchcore import hit

    assert [s.name for s in hit.registered()] == SEARCH_SOURCES
    assert isinstance(hit.SOURCES, Registry) and hit._REGISTRY is hit.SOURCES.table


def test_the_item_kinds_stay_a_plain_table_and_the_harnesses_list_the_same_names():
    from ddflow.config_sections._kinds import KINDS
    from ddflow.services import harnessreg

    # `config` may import nothing (tests/test_layering.py), so the item-kind table cannot be a
    # `core.registry.Registry`; it keeps its names and order.
    assert list(KINDS) == ITEM_KINDS and KINDS["doc"].base == "doc_pipeline"
    assert KINDS.get("nope") is None
    assert harnessreg.ids() == HARNESSES and harnessreg.get("claude").id == "claude"
    assert harnessreg.get("nonesuch") is None


# -- the ratchet on the number of idioms ---------------------------------------------------

#: Hand-rolled `register*()` functions that mutate a module-level table and have not moved to
#: `Registry` yet. This set may only shrink: a new seam uses `ddflow.core.registry`.
HAND_ROLLED = {
    "ddflow/core/schedule.py:register_filter",
    "ddflow/core/schedule.py:register_key",
    "ddflow/services/cadence.py:register_due",
    "ddflow/services/guidance/checks.py:register",
    "ddflow/services/guidance/review.py:register_trigger",
    "ddflow/services/metrics/providers.py:register",
    "ddflow/services/metrics/providers.py:register_dimension",
    "ddflow/services/metrics/providers.py:unregister",
    "ddflow/services/metrics/providers.py:unregister_dimension",
    "ddflow/services/migrations/__init__.py:register",
}
_MUTATORS = {"append", "add", "pop", "remove", "setdefault", "update"}


def _module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for n in tree.body:
        for t in n.targets if isinstance(n, ast.Assign) else [getattr(n, "target", None)]:
            if isinstance(t, ast.Name):
                names.add(t.id)
    return names


def _mutates(fn: ast.FunctionDef, names: set[str]) -> bool:
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign | ast.AugAssign):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            if any(
                isinstance(t, ast.Subscript)
                and isinstance(t.value, ast.Name)
                and t.value.id in names
                for t in targets
            ):
                return True
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id in names
            and sub.func.attr in _MUTATORS
        ):
            return True
    return False


def _hand_rolled() -> set[str]:
    found: set[str] = set()
    for path in sorted((ROOT / "ddflow").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "ddflow/core/registry.py":
            continue
        tree = ast.parse(path.read_text("utf-8"))
        names = _module_names(tree)
        for n in tree.body:
            if (
                isinstance(n, ast.FunctionDef)
                and re.match(r"^(un)?register(_\w+)?$", n.name)
                and _mutates(n, names)
            ):
                found.add(f"{rel}:{n.name}")
    return found


def test_no_new_hand_rolled_registry_and_none_left_in_the_baseline_that_moved():
    now = _hand_rolled()
    assert now <= HAND_ROLLED, (
        f"a new register() idiom: use ddflow.core.registry.Registry instead: "
        f"{sorted(now - HAND_ROLLED)}"
    )
    assert HAND_ROLLED <= now, (
        f"moved to Registry, drop from HAND_ROLLED: {sorted(HAND_ROLLED - now)}"
    )


def test_the_ratchet_sees_a_hand_rolled_idiom(tmp_path):
    tree = ast.parse("_T = {}\n\ndef register(name, fn):\n    _T[name] = fn\n")
    (fn,) = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert _mutates(fn, _module_names(tree))
    tree = ast.parse("_T = Registry()\n\ndef register(x):\n    return _T.register(x)\n")
    (fn,) = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert not _mutates(fn, _module_names(tree))
