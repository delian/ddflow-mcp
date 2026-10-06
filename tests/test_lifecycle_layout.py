"""B-split-api-lifecycle: `api/lifecycle` is a package, one module per operation area.

`from ddflow.api import lifecycle as L` and every `L.<name>` call site are unchanged: the
package re-exports every name the single module defined (and the modules it imported,
`L.L` among them), and `__all__` names exactly the public operations it exposed.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

from ddflow.api import lifecycle as LC
from ddflow.services import leases

#: What `api/lifecycle.py` exposed before the split -- the names no caller may lose.
PUBLIC = [
    "DEFAULT_CHECK_RECOVERY",
    "DEFAULT_NEXT_KIND",
    "DEFAULT_WAIT_TIMEOUT_S",
    "WAITABLE",
    "abandon",
    "block",
    "brief",
    "callers_tree",
    "claim",
    "complete",
    "heartbeat",
    "merge",
    "next_",
    "release",
    "remove",
    "unblock",
    "wait",
]
#: The single module was 2,332 lines; no area module should grow back toward that.
MAX_MODULE_LINES = 600


def _areas():
    return [
        importlib.import_module(f"{LC.__name__}.{m.name}")
        for m in pkgutil.iter_modules(LC.__path__)
    ]


def test_it_is_a_package_and_the_init_defines_nothing():
    init = Path(LC.__file__)
    assert init.name == "__init__.py"
    defs = [n for n in ast.parse(init.read_text("utf-8")).body if isinstance(n, ast.FunctionDef)]
    assert not defs, [d.name for d in defs]


def test_no_public_name_is_lost():
    assert sorted(LC.__all__) == PUBLIC
    for name in PUBLIC:
        assert getattr(LC, name) is not None, name
    assert callable(LC.claim) and callable(LC.wait) and callable(LC.brief)


def test_every_name_an_area_defines_is_re_exported():
    for mod in _areas():
        for name, obj in vars(mod).items():
            if getattr(obj, "__module__", None) == mod.__name__:
                assert getattr(LC, name) is obj, f"lifecycle.{name} is not {mod.__name__}.{name}"


def test_the_modules_the_single_file_imported_are_still_attributes():
    """`monkeypatch.setattr(A.L, "release", ...)` (tests/test_wait.py) reaches the leases
    module through the lifecycle namespace."""
    assert LC.L is leases


def test_no_area_module_is_a_monolith_again():
    for mod in _areas():
        n = len(Path(mod.__file__).read_text("utf-8").splitlines())
        assert n < MAX_MODULE_LINES, f"{mod.__name__} is {n} lines"
