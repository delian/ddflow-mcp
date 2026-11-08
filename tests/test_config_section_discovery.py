"""A new `[section]` is one ``ddflow/config_sections/<name>.py`` module: the registry discovers
it (a sorted scan, no hash order, duplicate names refuse) and ``ddflow.config`` wires the
dataclass fields and the class re-exports from that discovery, so neither ``config/__init__.py``
nor ``config/_core.py`` carries a per-section hand-kept list to edit."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_THROWAWAY = """\
from dataclasses import dataclass

from ddflow.config_sections._docs import declare, knob


@declare("zzz_throwaway")
@dataclass
class ZzzThrowawayConfig:
    zzz_count: int = knob(1, doc="throwaway knob for the discovery test")
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


def test_a_new_section_module_is_discovered_without_editing_shared_files(tmp_path, monkeypatch):
    """Adding a section to a package on the search path is discovered by the scan, registers via
    `@declare`, and leaves config/__init__.py and config/_core.py byte-identical."""
    import ddflow.config as C
    from ddflow.config import _core
    from ddflow.config_sections import _docs, __path__ as cs_path

    init_before = Path(C.__file__).read_bytes()
    core_before = Path(_core.__file__).read_bytes()
    baseline = set(_docs.SECTION_CLASSES)

    extra = tmp_path / "extra_sections"
    extra.mkdir()
    (extra / "__init__.py").write_text("")
    (extra / "zzz_throwaway.py").write_text(_THROWAWAY)
    monkeypatch.setattr("ddflow.config_sections.__path__", [*cs_path, str(extra)])

    from ddflow import config_sections as CS

    assert "zzz_throwaway" in CS.module_names(CS), "the new module is discovered by the scan"

    module = importlib.import_module(f"{CS.__name__}.zzz_throwaway")
    assert _docs.SECTION_CLASSES.get("zzz_throwaway") is module.ZzzThrowawayConfig
    assert set(_docs.SECTION_CLASSES) == baseline | {"zzz_throwaway"}  # only the throwaway added

    assert Path(C.__file__).read_bytes() == init_before, "config/__init__.py never changes"
    assert Path(_core.__file__).read_bytes() == core_before, "config/_core.py never changes"
