"""B-split-config: `config.py` is the loader -- each `[section]` lives in its own module.

Every new knob edits its section's module rather than one 2,400-line file every other
feature also edits. `from ddflow.config import Config, <Section>` keeps working: the
sections are re-exported, and they register their knob docs in declaration order.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import pkgutil
from pathlib import Path

import ddflow.config as C
from ddflow import config_sections
from ddflow.config_sections import SECTION_MODULES
from ddflow.config_sections._docs import KNOB_DOCS

CONFIG_PY = Path(C.__file__)
#: config.py was 2,445 lines with every section in it; the split left about 920.
MAX_CONFIG_LINES = 1000


def _dataclasses_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text("utf-8"))
    return [
        n.name
        for n in tree.body
        if isinstance(n, ast.ClassDef)
        and any(
            getattr(d, "id", getattr(getattr(d, "func", None), "id", "")) == "dataclass"
            for d in n.decorator_list
        )
    ]


def _section_types() -> dict[str, type]:
    return {
        f.name: type(getattr(C.Config(), f.name))
        for f in dataclasses.fields(C.Config)
        if dataclasses.is_dataclass(getattr(C.Config(), f.name, None))
    }


def test_config_py_stays_small():
    n = len(CONFIG_PY.read_text("utf-8").splitlines())
    assert n < MAX_CONFIG_LINES, f"config.py is {n} lines; a section belongs in config_sections/"


def test_config_py_declares_no_section():
    assert _dataclasses_in(CONFIG_PY) == ["Config"]


def test_each_section_module_holds_exactly_one_section():
    for mod in pkgutil.iter_modules(config_sections.__path__):
        if mod.name.startswith("_"):
            continue
        path = Path(config_sections.__path__[0]) / f"{mod.name}.py"
        found = _dataclasses_in(path)
        assert len(found) == 1, f"config_sections/{mod.name}.py holds {found}"


def test_every_section_comes_from_its_module_and_is_re_exported():
    modules = {
        importlib.import_module(f"{config_sections.__name__}.{m.name}")
        for m in pkgutil.iter_modules(config_sections.__path__)
        if not m.name.startswith("_")
    }
    assert modules == set(SECTION_MODULES)
    for section, cls in _section_types().items():
        assert cls.__module__.startswith(config_sections.__name__ + "."), (section, cls)
        assert getattr(C, cls.__name__) is cls, f"ddflow.config.{cls.__name__} is not re-exported"
    assert len(set(_section_types().values())) == len(SECTION_MODULES)


def test_the_knob_docs_are_one_registry():
    assert C.KNOB_DOCS is KNOB_DOCS
    explained = [k for k, *_ in C.Config().explain()]
    assert explained and all(k in KNOB_DOCS for k in explained)
