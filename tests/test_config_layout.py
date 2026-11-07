"""B-split-config: `config/` is a package -- `_core.py` is the loader, `_decls.py` the
value sets, `_coerce.py` the value coercion and `config_sections/` one `[section]` per
module.

Every new knob edits its section's module rather than one 2,400-line file every other
feature also edits. `from ddflow.config import Config, <Section>` keeps working: the
package re-exports the sections (in declaration order) and the loader names that used to
live in `config.py`.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import pkgutil
import re
from pathlib import Path

import ddflow.config as C
from ddflow import config_sections
from ddflow.config_sections import SECTION_MODULES
from ddflow.config_sections._docs import KNOB_DOCS

CONFIG_INIT = Path(C.__file__)
CONFIG_DIR = CONFIG_INIT.parent
#: `config.py` was 2,445 lines with every section in it; the split into `config/` keeps
#: each submodule under the one size limit, the knob [quality].max_file_bytes ([r-small-files]).
MAX_MODULE_BYTES = C.Config().quality.max_file_bytes


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


def test_each_config_submodule_is_small():
    for path in CONFIG_DIR.glob("*.py"):
        size = path.stat().st_size
        assert MAX_MODULE_BYTES == 0 or size <= MAX_MODULE_BYTES, (  # 0: the check is off
            f"{path.name} is {size} bytes; a new knob belongs in config_sections/"
        )


def test_config_init_declares_no_dataclass():
    assert _dataclasses_in(CONFIG_INIT) == []


def test_the_loader_dataclass_lives_in_core():
    assert _dataclasses_in(CONFIG_DIR / "_core.py") == ["Config"]


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


def test_a_section_modules_public_tables_are_re_exported():
    """B-config-reexport-lost-export-table-keys: the package split dropped a public
    name of `config.py`. `config_sections` states `ddflow.config` re-exports every name
    it defines; a pre-split public table must stay importable from `ddflow.config`."""
    from ddflow.config_sections import export

    assert hasattr(C, "EXPORT_TABLE_KEYS"), "ddflow.config dropped EXPORT_TABLE_KEYS"
    assert C.EXPORT_TABLE_KEYS is export.EXPORT_TABLE_KEYS


def test_a_section_is_wired_from_declare_not_a_kept_list():
    """B-config-section-needs-hub-edit: a section is named only by its own `@declare()`.

    `Config` assembles its fields, and `ddflow.config` its re-exports, from the
    `SECTION_CLASSES` registry `declare()` populates -- `_core.py` no longer hand-lists a
    field per section and `config/__init__.py` no longer re-exports a `*Config` name per
    section, so a new section no longer takes a lease on either hub file. On the unfixed
    code `_core.py` still hand-listed each field, so this test's source (`SECTION_CLASSES`)
    did not exist and it failed to import."""
    from ddflow.config_sections._docs import SECTION_CLASSES

    assert _section_types() == dict(SECTION_CLASSES)
    for cls in SECTION_CLASSES.values():
        assert getattr(C, cls.__name__) is cls
    # The mechanism, not just the mapping it yields: `_core.py` must not hand-list a field
    # per section (the hub edit this bug removes). Its bookkeeping fields read
    # `field(default_factory=list/dict/...)`; a hand-kept section field would read
    # `<name>: <Something>Config = field(`.
    core_src = (CONFIG_DIR / "_core.py").read_text("utf-8")
    assert not re.search(r":\s*\w+Config\s*=\s*field\(", core_src), (
        "_core.py hand-lists a section field again; Config must assemble its fields from "
        "SECTION_CLASSES"
    )
    # The other hub file: `config/__init__.py` re-exports each `<Something>Config` class
    # lazily through `__getattr__`, so no section class name may be spelled there either.
    init_src = CONFIG_INIT.read_text("utf-8")
    spelled = sorted(cls.__name__ for cls in SECTION_CLASSES.values() if cls.__name__ in init_src)
    assert not spelled, (
        "ddflow.config re-exports section classes by hand again: "
        + ", ".join(spelled)
    )


def test_a_star_import_binds_the_section_classes():
    """B-config-section-needs-hub-edit: `from ddflow.config import *` binds the section
    classes, which lazy `__getattr__` alone would not (a star-import reads `__dict__` or
    `__all__`, never `__getattr__`; `__all__` must name them)."""
    from ddflow.config_sections._docs import SECTION_CLASSES

    namespace: dict[str, object] = {}
    exec("from ddflow.config import *", namespace)
    assert namespace["AgentConfig"] is SECTION_CLASSES["agent"]
    assert namespace["LeaseConfig"] is SECTION_CLASSES["lease"]


def test_the_knob_docs_are_one_registry():
    assert C.KNOB_DOCS is KNOB_DOCS
    explained = [k for k, *_ in C.Config().explain()]
    assert explained and all(k in KNOB_DOCS for k in explained)


def test_a_section_module_documents_only_its_own_knobs():
    """Review finding: `[gates]` and `[reinstruct]` docs once sat in the `[cadence]` module."""
    section_of = {cls.__module__: name for name, cls in _section_types().items()}
    for mod in SECTION_MODULES:
        tree = ast.parse(Path(mod.__file__).read_text("utf-8"))
        named = {
            n.args[0].value
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", "") == "_doc"
            and n.args
            and isinstance(n.args[0], ast.Constant)
        }
        assert named <= {section_of[mod.__name__]}, f"{mod.__name__} documents {sorted(named)}"
