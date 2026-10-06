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


def _model_imports(n: ast.AST) -> list[str]:
    """What an import statement pulls in that is the `model` module, in any spelling."""
    if isinstance(n, ast.Import):
        return [a.name for a in n.names if a.name.split(".")[-1] == "model"]
    if not isinstance(n, ast.ImportFrom):
        return []
    base = (n.module or "").split(".")
    hits = [n.module] if base[-1] == "model" else []
    hits += [a.name for a in n.names if not n.module or base[-1] == "core"]
    return [h for h in hits if h and h.split(".")[-1] == "model"]


def test_no_handler_module_imports_the_model():
    """`model` imports the handlers; the reverse edge would be a cycle. Every spelling:
    `from ..model import x`, `from .. import model`, `from ddflow.core import model`,
    `import ddflow.core.model`."""
    for mod in _handler_modules():
        tree = ast.parse(Path(mod.__file__).read_text("utf-8"))
        for n in ast.walk(tree):
            hits = _model_imports(n)
            assert not hits, f"{mod.__name__}:{n.lineno} imports the model ({hits})"


def test_the_import_guard_can_fail():
    """Planted: each spelling of the reverse edge is caught, and an unrelated one is not."""
    for line in (
        "from ..model import fold",
        "from .. import model",
        "from ddflow.core import model",
        "import ddflow.core.model",
    ):
        assert _model_imports(ast.parse(line).body[0]), line
    assert not _model_imports(ast.parse("from ..records import State").body[0])
