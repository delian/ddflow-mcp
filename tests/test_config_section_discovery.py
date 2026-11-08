"""A new `[section]` is one ``ddflow/config_sections/<name>.py`` module: the registry discovers
it (a sorted scan, no hash order, duplicate names refuse) and ``ddflow.config`` wires the
dataclass fields and the class re-exports from that discovery, so neither ``config/__init__.py``
nor ``config/_core.py`` carries a per-section hand-kept list to edit."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_THROWAWAY = """\
from dataclasses import dataclass

from ddflow.config_sections._docs import declare, knob


@declare("zzz_throwaway")
@dataclass
class ZzzThrowawayConfig:
    zzz_count: int = knob(1, doc="throwaway knob for the discovery test")
    zzz_mode: str = knob(
        "auto",
        choices=("auto", "strict"),
        strictest=("strict", "the safe fallback for the discovery test"),
        doc="an enum knob, for the table wiring",
    )
"""


def test_section_names_are_the_discovery_scan():
    import ddflow.config_sections as CS

    assert CS.SECTION_NAMES == CS.module_names(CS), "SECTION_NAMES must be the scan, not a list"
    assert CS.SECTION_NAMES == tuple(sorted(CS.SECTION_NAMES)), "deterministic, not hash order"
    assert all(not n.startswith("_") for n in CS.SECTION_NAMES), "plumbing modules are not sections"


def test_a_duplicate_section_name_is_refused():
    from ddflow.config_sections import _docs

    name = next(iter(_docs.SECTION_CLASSES))
    with pytest.raises(ValueError, match="declared twice"):
        _docs.declare(name)(type("DupConfig", (), {}))


def test_config_fields_classes_and_knob_manifest_come_from_the_registry():
    """The seam: `ddflow.config` assembles Config's section fields, the class re-exports and the
    knob manifest from `SECTION_CLASSES` (the discovery), never from a per-section hand-kept
    list. A revert to a hand list fails here."""
    import ddflow.config as C
    from ddflow.config_sections import _docs

    assert set(C._SECTION_CLASS_BY_NAME) == {c.__name__ for c in _docs.SECTION_CLASSES.values()}

    cfg = C.Config()
    for section, cls in _docs.SECTION_CLASSES.items():
        assert hasattr(cfg, section), f"[{section}] not wired into Config"
        assert getattr(C, cls.__name__) is cls, f"{cls.__name__} not re-exported"
        for name, field in cls.__dataclass_fields__.items():
            if field.metadata.get(_docs.KNOB) is not None:  # a knob field must be manifest
                assert f"{section}.{name}" in _docs.KNOB_DOCS, f"{section}.{name} missing"


def test_a_new_section_module_is_discovered_without_editing_shared_files(tmp_path, monkeypatch):
    """Adding a section to a package on the search path is discovered by the scan, registers via
    `@declare`, and leaves config/__init__.py and config/_core.py byte-identical."""
    from ddflow.config import _core
    from ddflow.config_sections import __path__ as cs_path
    from ddflow.config_sections import _docs

    section_before = dict(_docs.SECTION_CLASSES)
    knob_docs_before = dict(_docs.KNOB_DOCS)
    declared_before = dict(_docs.DECLARED)
    declared_in_before = dict(_docs.DECLARED_IN)

    extra = tmp_path / "extra_sections"
    extra.mkdir()
    (extra / "__init__.py").write_text("")
    (extra / "zzz_throwaway.py").write_text(_THROWAWAY)
    monkeypatch.setattr("ddflow.config_sections.__path__", [*cs_path, str(extra)])

    from ddflow import config_sections as CS

    assert "zzz_throwaway" in CS.module_names(CS), "the new module is discovered by the scan"

    module = importlib.import_module(f"{CS.__name__}.zzz_throwaway")
    try:
        assert _docs.SECTION_CLASSES.get("zzz_throwaway") is module.ZzzThrowawayConfig
        assert set(_docs.SECTION_CLASSES) == set(section_before) | {"zzz_throwaway"}
        assert "zzz_throwaway.zzz_count" in _docs.KNOB_DOCS  # the knob manifest absorbs it

        # The assembly seam, fresh: a Config built AFTER the section declared wires it from
        # SECTION_CLASSES (the discovery), so a hand-kept field list in config/_core.py is what
        # this catches -- the field must come from the registry, not a literal.
        from dataclasses import dataclass

        from ddflow.config import _core

        @dataclass
        class _Fresh(_core._SectionFields):
            bookkeeping: int = 0

        assert hasattr(_Fresh(), "zzz_throwaway"), "a fresh Config does not wire the new section"
        assert isinstance(_Fresh().zzz_throwaway, module.ZzzThrowawayConfig)
    finally:
        # roll back every registry @declare touched, so no phantom knob leaks to later tests.
        _docs.SECTION_CLASSES.clear(), _docs.SECTION_CLASSES.update(section_before)
        _docs.KNOB_DOCS.clear(), _docs.KNOB_DOCS.update(knob_docs_before)
        _docs.DECLARED.clear(), _docs.DECLARED.update(declared_before)
        _docs.DECLARED_IN.clear(), _docs.DECLARED_IN.update(declared_in_before)
        sys.modules.pop(f"{CS.__name__}.zzz_throwaway", None)

    # The rollback left the registries exactly as they were: nothing leaked past the test.
    assert _docs.SECTION_CLASSES == section_before
    assert _docs.KNOB_DOCS == knob_docs_before
    assert _docs.DECLARED == declared_before
    assert _docs.DECLARED_IN == declared_in_before


def test_a_new_section_wires_into_a_fresh_config_process(tmp_path):
    """Definitive seam proof: in a fresh process, the section module registered on the search
    path BEFORE `ddflow.config` imports is wired into the real `Config`, its class re-export and
    the knob manifest -- without editing config/__init__.py or config/_core.py."""
    import subprocess

    import ddflow as _ddflow

    root = str(Path(_ddflow.__file__).parents[1])
    extra = tmp_path / "extra_sections"
    extra.mkdir()
    (extra / "__init__.py").write_text("")
    (extra / "zzz_throwaway.py").write_text(_THROWAWAY)

    script = "\n".join(
        [
            "import importlib, sys",
            f"sys.path.insert(0, {root!r})",
            "import ddflow.config_sections as CS",  # runs discovery before ddflow.config
            f"CS.__path__ = [*CS.__path__, {str(extra)!r}]",
            "m = importlib.import_module('ddflow.config_sections.zzz_throwaway')",  # @declare registers
            "import ddflow.config as C",  # Config is assembled now, after the section declared
            "cfg = C.Config()",
            "assert hasattr(cfg, 'zzz_throwaway'), 'Config did not wire the new section'",
            "assert getattr(C, 'ZzzThrowawayConfig') is m.ZzzThrowawayConfig, 'class not re-exported'",
            "from ddflow.config_sections import _docs as D",
            "assert 'zzz_throwaway' in D.SECTION_CLASSES",
            "assert 'zzz_throwaway.zzz_count' in D.KNOB_DOCS",
            "from ddflow.config import _decls as L",  # the enum table is wired too
            "assert L.KNOB_CHOICES['zzz_throwaway.zzz_mode'] == ('auto', 'strict')",
            "print('wired')",
        ]
    )
    ran = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=180
    )
    assert ran.returncode == 0, ran.stderr
