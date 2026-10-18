"""DX-registry-files-shared: a command, a section and a constant register by convention."""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.config_sections import SECTION_NAMES
from ddflow.services import constants_audit as ca
from ddflow.services.help import grouped_tools
from ddflow.surfaces.declared import all_commands, family_modules
from ddflow.surfaces.tools import tiers


def test_every_declared_family_file_is_found_without_being_named():
    stems = {m.__name__.rsplit(".", 1)[1] for m in family_modules()}
    assert {"health", "mergequeue", "lifecycle", "knowledge"} <= stems
    assert not stems & {"lifecycle_cli", "mutation_params"}, "helpers declare no COMMANDS"
    assert len(all_commands()) > 100


def test_every_config_section_file_is_found_without_being_named():
    assert {"health", "merge_queue", "lease"} <= set(SECTION_NAMES)
    assert not any(n.startswith("_") for n in SECTION_NAMES)


def test_a_declared_tool_outside_the_legacy_sets_needs_a_tier(monkeypatch):
    from dataclasses import replace

    from ddflow.surfaces import tools

    legacy = tiers._LEGACY_CORE | tiers._LEGACY_STANDARD | tiers._LEGACY_FULL
    fresh = replace(tools.DECLARED["ddflow_health"], tool="ddflow_zz_fresh", tier="")
    assert "ddflow_zz_fresh" not in legacy
    monkeypatch.setitem(tools.DECLARED, "ddflow_zz_fresh", fresh)
    with pytest.raises(RuntimeError, match="ddflow_zz_fresh"):
        tiers._declared("core")
    monkeypatch.setitem(tools.DECLARED, "ddflow_zz_fresh", replace(fresh, tier="core"))
    assert "ddflow_zz_fresh" in tiers._declared("core")
    assert "ddflow_zz_fresh" not in tiers._declared("all")


def test_a_tool_names_its_own_help_group():
    tools = {
        "ddflow_next": {},
        "ddflow_zz_fresh": {"help_group": "Brand new family"},
        "ddflow_zz_other": {"help_group": "Brand new family"},
        "ddflow_zz_stray": {},
    }
    groups = dict(grouped_tools(tools))
    assert groups["Brand new family"] == ["ddflow_zz_fresh", "ddflow_zz_other"]
    assert groups["Unmapped — add a prefix to `_GROUPS`"] == ["ddflow_zz_stray"]


def test_a_help_group_in_a_tool_entry_comes_from_the_command():
    from dataclasses import replace

    from ddflow.surfaces.tools import DECLARED

    entry = replace(DECLARED["ddflow_health"], help_group="X").tool_entry()
    assert entry["help_group"] == "X"
    assert "help_group" not in DECLARED["ddflow_health"].tool_entry()


def test_the_constants_register_reads_fragments_beside_it(tmp_path: Path):
    (tmp_path / "docs" / "ddflow" / "constants-exemptions.d").mkdir(parents=True)
    (tmp_path / ca.REGISTER).write_text('[knob]\n"a.py:A_MAX" = "a.max"\n')
    (tmp_path / ca.FRAGMENTS / "b.toml").write_text(
        '[exempt]\n"b.py:B_MAX" = "structural"\n[knob]\n"b.py:B_LIMIT" = "b.limit"\n'
    )
    reg = ca.load_register(tmp_path)
    assert reg.knob == {"a.py:A_MAX": "a.max", "b.py:B_LIMIT": "b.limit"}
    assert reg.exempt == {"b.py:B_MAX": "structural"}


def test_a_docstring_line_starting_with_commands_does_not_register_a_module():
    from ddflow.surfaces.declared import _HAS_COMMANDS

    assert not _HAS_COMMANDS.search('"""Helpers.\n\nCOMMANDS that use these.\n"""\n')
    assert _HAS_COMMANDS.search("COMMANDS: tuple[Command, ...] = (\n")
    assert _HAS_COMMANDS.search("COMMANDS = ()\n")


def test_a_module_the_text_match_fools_is_skipped_not_crashed_on(tmp_path, monkeypatch):
    import ddflow.surfaces.declared as D

    (tmp_path / "zz_fake.py").write_text('X = """\nCOMMANDS = 1\n"""\n')
    monkeypatch.setattr(D, "__file__", str(tmp_path / "__init__.py"))
    monkeypatch.setattr(D, "__path__", [str(tmp_path)])
    import sys

    sys.path.insert(0, str(tmp_path.parent))
    try:
        assert all(m.__name__ != f"{D.__name__}.zz_fake" for m in D.family_modules())
    finally:
        sys.path.remove(str(tmp_path.parent))


def test_the_tools_only_scan_misses_no_tool_the_full_scan_finds():
    from ddflow.surfaces.tools import DECLARED

    assert {c.tool for c in all_commands() if c.tool} == set(DECLARED)
