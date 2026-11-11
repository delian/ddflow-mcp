"""B-mcp-shell-version-skew: a stale MCP server (older code, newer tree) must not raise on
a config knob it predates.

A stdio MCP server is a long-lived process. When it runs from the source tree (or an
editable install), `_is_code_tree` sees the SAME path for the config root and the code,
so a config file that a sibling session has moved on creates would be read strict -- and
an unknown knob raised, where it is the newer release's and must be reported, not raised
(the 0.2.10 -> 0.2.11 bump's `lease.reconcile_tests`). The strict read is right for a
fresh process (same path AND same version); only a version the code outlasted greys it.

The other half: `installed_version()` for a source tree / editable install is what the tree
declares on disk NOW, so `stale_facts` can finally see an on-disk bump and `[mcp].on_upgrade
= reexec` replaces the stale server instead of concluding a restart would change nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import ddflow
from ddflow import config as DCFG
from ddflow.config import Config
from ddflow.config._tree import declared_version

DECLARED = "0.2.11"
RUNNING = "0.2.10"


def _tree(tmp_path, monkeypatch, running: str = RUNNING):
    """A tmp dir that IS the code tree (`_CODE_TREE`) for the duration, holding a config
    with a knob this (simulated) code does not know."""
    tree = tmp_path / "tree"
    (tree / "ddflow").mkdir(parents=True)
    (tree / "ddflow" / "__init__.py").write_text(f'__version__ = "{DECLARED}"\n')
    (tree / ".ddflow").mkdir()
    (tree / ".ddflow" / "config.toml").write_text(
        "[lease]\nttl_s = 77\nfrom_the_future = true\n"
    )
    monkeypatch.setattr(ddflow, "__version__", running)
    monkeypatch.setattr(DCFG, "_CODE_TREE", tree.resolve())
    return tree


def test_a_stale_code_tree_greys_an_unknown_knob_not_raise(tmp_path, monkeypatch):
    """The code outlived the tree's bump: the unknown knob is a newer release's, recorded,
    the knobs this code knows still apply -- never raised (the MCP read path keeps working)."""
    tree = _tree(tmp_path, monkeypatch)
    cfg = Config.load(tree)
    assert cfg.lease.ttl_s == 77
    assert "lease.from_the_future" in cfg.unknown_knobs


def test_a_fresh_code_tree_still_refuses_an_unknown_knob(tmp_path, monkeypatch):
    """Same path AND the version this process imported equals the declared one: the file and
    the code are one commit again, so an unknown knob can only be a typo -- it stays strict."""
    tree = _tree(tmp_path, monkeypatch, running=DECLARED)
    with pytest.raises(ValueError, match=r"unknown knob 'lease\.from_the_future'"):
        Config.load(tree)


def test_declared_version_reads_the_literal_from_disk(tmp_path):
    (tmp_path / "ddflow").mkdir()
    (tmp_path / "ddflow" / "__init__.py").write_text(f'__version__ = "{DECLARED}"\n')
    assert declared_version(tmp_path) == DECLARED


def test_declared_version_is_empty_when_the_tree_has_no_literal(tmp_path):
    (tmp_path / "ddflow").mkdir()
    (tmp_path / "ddflow" / "__init__.py").write_text("x = 1\n")
    assert declared_version(tmp_path) == ""


def test_declared_version_is_empty_for_a_non_utf8_file(tmp_path):
    """A non-UTF-8 `__init__.py` raises UnicodeDecodeError (a ValueError), not OSError: it
    must be a blank answer, never an abort (the critic's finding on the first cut)."""
    (tmp_path / "ddflow").mkdir()
    (tmp_path / "ddflow" / "__init__.py").write_bytes(b'__version__ = "\xff"\n')
    assert declared_version(tmp_path) == ""


def test_declared_version_is_empty_when_the_file_is_missing(tmp_path):
    assert declared_version(tmp_path / "missing") == ""


def test_declared_version_accepts_a_crlf_literal(tmp_path):
    (tmp_path / "ddflow").mkdir()
    (tmp_path / "ddflow" / "__init__.py").write_bytes(b'__version__ = "0.2.11"\r\n')
    assert declared_version(tmp_path) == "0.2.11"


def test_installed_version_reads_a_source_tree_from_disk(tmp_path, monkeypatch):
    """A source tree IS its own install: its "installed" version is the one on disk, so a
    stale server stops concluding a restart would change nothing."""
    from ddflow.services import upgrade_notice as UN

    monkeypatch.setattr(
        UN, "install_info", lambda: SimpleNamespace(kind="source-tree", version=RUNNING)
    )
    monkeypatch.setattr(UN._paths, "package_parent", lambda: tmp_path)
    (tmp_path / "ddflow").mkdir()
    (tmp_path / "ddflow" / "__init__.py").write_text(f'__version__ = "{DECLARED}"\n')
    assert UN.installed_version() == DECLARED


def test_installed_version_reads_an_editable_install_from_disk(tmp_path, monkeypatch):
    """The editable arm of `installed_version` shares the source-tree path: it too reads the
    on-disk literal, whose metadata version is the frozen import version."""
    from ddflow.services import upgrade_notice as UN

    monkeypatch.setattr(
        UN, "install_info", lambda: SimpleNamespace(kind="editable", version=RUNNING)
    )
    monkeypatch.setattr(UN._paths, "package_parent", lambda: tmp_path)
    (tmp_path / "ddflow").mkdir()
    (tmp_path / "ddflow" / "__init__.py").write_text(f'__version__ = "{DECLARED}"\n')
    assert UN.installed_version() == DECLARED
