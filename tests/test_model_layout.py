"""B-split-model-handlers: `core/model.py` is the fold -- `HANDLERS`, `known_kinds`, `fold`.

The record dataclasses and `State` live in `core/records.py` and the event handlers in
`core/handlers/<domain>.py`. Every name stays importable from `ddflow.core.model`, and
the fold is unchanged: the same handler for every kind, in the same order.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

from ddflow.core import handlers, model, records

MODEL_PY = Path(model.__file__)
#: model.py was 2,505 lines with every record and handler in it; the split left ~350.
MAX_MODEL_LINES = 450


def _defs(path: Path) -> list[ast.AST]:
    return ast.parse(path.read_text("utf-8")).body


def _handler_modules():
    return [
        importlib.import_module(f"{handlers.__name__}.{m.name}")
        for m in pkgutil.iter_modules(handlers.__path__)
    ]


def test_model_py_stays_small():
    n = len(MODEL_PY.read_text("utf-8").splitlines())
    assert n < MAX_MODEL_LINES, f"model.py is {n} lines; records and handlers live elsewhere"


def test_model_py_defines_no_handler_and_no_record():
    names = [n.name for n in _defs(MODEL_PY) if isinstance(n, ast.FunctionDef | ast.ClassDef)]
    assert not [n for n in names if n.startswith("_h_")], names
    assert not [n for n in _defs(MODEL_PY) if isinstance(n, ast.ClassDef)], names


def test_every_handler_lives_in_a_domain_module_and_is_re_exported():
    seen: dict[str, str] = {}
    for mod in _handler_modules():
        for name in dir(mod):
            fn = getattr(mod, name)
            if name.startswith("_h_") and getattr(fn, "__module__", "") == mod.__name__:
                assert name not in seen, f"{name} defined in {seen[name]} and {mod.__name__}"
                seen[name] = mod.__name__
                assert getattr(model, name) is fn, f"model.{name} is not re-exported"
    assert len(seen) > 40, f"the scan found almost no handlers: {sorted(seen)}"


def test_the_records_are_re_exported():
    for name, obj in vars(records).items():
        if isinstance(obj, type) and obj.__module__ == records.__name__:
            assert getattr(model, name) is obj, f"model.{name} is not re-exported"


def test_no_handler_module_imports_the_model():
    """`model` imports the handlers; the reverse edge would be a cycle."""
    for mod in _handler_modules():
        tree = ast.parse(Path(mod.__file__).read_text("utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.level:
                assert n.module != "model", f"{mod.__name__} imports ..model"
