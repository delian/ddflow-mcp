"""A new `[section]` is one ``ddflow/config_sections/<name>.py`` module: the registry discovers
it (a sorted scan, no hash order, duplicate names refuse) and ``ddflow.config`` wires the
dataclass fields and the class re-exports from that discovery, so neither ``config/__init__.py``
nor ``config/_core.py`` carries a per-section hand-kept list to edit."""

from __future__ import annotations

import pytest


def test_section_names_are_a_sorted_scan_of_public_modules():
    import ddflow.config_sections as CS

    names = CS.SECTION_NAMES
    assert names == tuple(sorted(names)), "discovery order must be deterministic, not hash order"
    assert all(not n.startswith("_") for n in names), "plumbing modules are not sections"


def test_a_duplicate_section_name_is_refused():
    from ddflow.config_sections import _docs

    name = next(iter(_docs.SECTION_CLASSES))
    with pytest.raises(ValueError, match="declared twice"):
        _docs.declare(name)(type("DupConfig", (), {}))


def test_config_wires_fields_and_class_reexports_from_the_discovery():
    import ddflow.config as C
    from ddflow.config_sections import _docs

    cfg = C.Config()
    names = {cls.__name__ for cls in _docs.SECTION_CLASSES.values()}
    assert set(C._SECTION_CLASS_BY_NAME) == names, "no section class may live outside the registry"
    for section, cls in _docs.SECTION_CLASSES.items():
        assert getattr(C, cls.__name__) is cls, f"{cls.__name__} not re-exported"
        assert hasattr(cfg, section), f"[{section}] not wired into Config"
