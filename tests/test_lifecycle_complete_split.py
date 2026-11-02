"""SZ-split-lifecycle-complete: `api/lifecycle/complete` is a package, one module per concern.

Every name the single module defined (public and private) is still an attribute of the
package and is the object its defining submodule holds, so `lifecycle.complete.<name>` and
every `from ...complete import <name>` are unchanged. A test that REPLACES a name patches the
submodule that defines it.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

CP = importlib.import_module("ddflow.api.lifecycle.complete")  # the package, not the function

GOVERNS = ("ddflow/api/lifecycle/complete/**",)

#: Every top-level definition of the former `complete.py`, plus `fold` (re-exported from it
#: by `api/lifecycle/__init__.py`).
DEFINED = """
    _abandon_refused _approved_force _ask_hint _auto_complete_unavailable
    _close_arg_refusal _close_bugs _complete_umbrellas_above _dispose
    _failed_review _na_map _only_unavailable _override_by_approval _person_force
    _phase_end_self_assessment _phase_suite _refuted_extra _rename
    _umbrella_children abandon block complete fold remove unblock
""".split()

MAX_BYTES = 20_000


def _subs():
    return [
        importlib.import_module(f"{CP.__name__}.{m.name}")
        for m in pkgutil.iter_modules(CP.__path__)
    ]


def test_every_old_name_is_reachable_and_dir_lists_it():
    for name in DEFINED:
        assert getattr(CP, name) is not None, name
    assert set(DEFINED) <= set(dir(CP))


def test_each_name_is_the_object_its_submodule_defines_and_has_one_home():
    homes: dict[str, str] = {}
    for mod in _subs():
        for n in ast.parse(Path(mod.__file__).read_text("utf-8")).body:
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                assert n.name not in homes, f"{n.name} in {homes[n.name]} and {mod.__name__}"
                homes[n.name] = mod.__name__
                assert getattr(CP, n.name) is getattr(mod, n.name), n.name
    assert set(DEFINED) - {"fold"} <= set(homes)


def test_submodule_names_do_not_shadow_a_lazy_attribute():
    names = {m.name for m in pkgutil.iter_modules(CP.__path__)}
    assert not names & set(DEFINED), names & set(DEFINED)


def test_modules_are_small_and_documented():
    for mod in _subs():
        path = Path(mod.__file__)
        assert path.stat().st_size < MAX_BYTES, mod.__name__
        assert ast.get_docstring(ast.parse(path.read_text("utf-8"))), mod.__name__
