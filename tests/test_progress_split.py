"""RF-core-progress-split: pin that the `ddflow/core/progress` package re-exports the whole
split surface, mirroring tests/test_module_splits.py (which pins the three B-uni-splits).

Every submodule's public definition is mapped in ``_SUBMODULE_OF`` and is the same object
on the package, so ``from ddflow.core.progress import new_reports_line`` binds the defined
name exactly as before; a name a submodule defines but the map drops would not reach the
package.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

#: Reads the package's submodules from disk (``pkgutil.iter_modules``), so a submodule
#: added to the package is a change no import reaches: a change under any of them selects
#: this test.
GOVERNS = ("ddflow/core/progress/**",)

PKG = "ddflow.core.progress"

#: The submodules stay under 30k chars each (rule r-small-files).
MAX_CHARS = 30_000


@pytest.fixture
def pkg():
    return importlib.import_module(PKG)


def _submodules(pkg):
    return [importlib.import_module(f"{PKG}.{m.name}") for m in pkgutil.iter_modules(pkg.__path__)]


def test_every_mapped_name_is_the_submodule_object(pkg):
    for name, sub in pkg._SUBMODULE_OF.items():
        obj = getattr(importlib.import_module(f"{PKG}.{sub}"), name)
        assert getattr(pkg, name) is obj, f"{PKG}.{name} is not {sub}.{name}"


def test_every_public_definition_a_submodule_makes_is_mapped(pkg):
    for mod in _submodules(pkg):
        tree = ast.parse(Path(mod.__file__).read_text("utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if not node.name.startswith("_"):
                    assert node.name in pkg._SUBMODULE_OF, (
                        f"{mod.__name__}.{node.name} is not re-exported"
                    )


def test_no_submodule_is_a_monolith(pkg):
    for mod in _submodules(pkg):
        n = len(Path(mod.__file__).read_text("utf-8"))
        assert n < MAX_CHARS, f"{mod.__name__} is {n} chars"
