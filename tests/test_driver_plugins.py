"""The driver-plugin SDK: registration is operator-only, a plugin conforms before it loads.

Third-party drivers (operator decision D-DRV-Q1, option C) register as an in-process
``entry_point`` (an object that builds a ``Lane``) or an external ``executable`` speaking a
versioned JSON-over-stdio protocol. Nothing runs before conformance, and an unknown protocol
version is refused, so every test here is offline: no spawned process, no installed agent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.adapters.lanes import Lane
from ddflow.services import driverplugins as DP


def _write_plugin(root: Path, user_home: Path, text: str) -> None:
    """Write ``text`` as the local (operator) layer's registration file."""
    path = DP.registration_files(root, user_home)[-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def test_an_unregistered_plugin_never_loads(tmp_path: Path):
    with pytest.raises(DP.PluginError):
        DP.load("no-such-plugin", root=tmp_path, user_home=tmp_path / "home")


def test_a_project_layer_cannot_register_a_plugin(tmp_path: Path):
    # The project layer (.ddflow/driver_plugins.toml, a clone) may never register a program.
    project = tmp_path / ".ddflow" / "driver_plugins.toml"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        '[[plugin]]\nname = "sneaky"\nkind = "executable"\nargv = ["/bin/sh"]\n'
    )
    assert "sneaky" not in DP.discover_plugins(root=tmp_path, user_home=tmp_path / "home")
    assert not any(
        p.name == "driver_plugins.toml"
        and str(p.parent) == str(tmp_path / ".ddflow")
        for p in DP.registration_files(tmp_path, tmp_path / "home")
    )
    with pytest.raises(DP.PluginError):
        DP.load("sneaky", root=tmp_path, user_home=tmp_path / "home")


def test_a_plugin_failing_conformance_is_refused(tmp_path: Path):
    # system.Env is a real ddflow object, but it is not a Lane: it does not build one.
    _write_plugin(
        tmp_path,
        tmp_path / "home",
        '[[plugin]]\nname = "bad"\nkind = "entry_point"\nentry_point = "ddflow.adapters.env:SystemEnv"\n',
    )
    with pytest.raises(DP.PluginError):
        DP.load("bad", root=tmp_path, user_home=tmp_path / "home")


def test_an_unknown_protocol_version_is_refused(tmp_path: Path):
    _write_plugin(
        tmp_path,
        tmp_path / "home",
        '[[plugin]]\nname = "future"\nkind = "executable"\nprotocol = 99\nargv = ["future-driver"]\n',
    )
    with pytest.raises(DP.PluginError):
        DP.discover_plugins(tmp_path, tmp_path / "home")


def test_a_conforming_entry_point_builds_a_lane(tmp_path: Path):
    _write_plugin(
        tmp_path,
        tmp_path / "home",
        '[[plugin]]\nname = "fake"\nkind = "entry_point"\nentry_point = "ddflow.adapters.fakes.lanes:FakeLane"\n',
    )
    lane = DP.load("fake", root=tmp_path, user_home=tmp_path / "home")
    assert isinstance(lane, Lane)


def test_a_wrong_layer_file_is_not_a_registration_file(tmp_path: Path):
    # The local layer is operator-owned and read; the bare project file is not read at all.
    files = DP.registration_files(tmp_path, tmp_path / "home")
    assert any(".ddflow/local" in str(p) for p in files)
    assert not any(str(p) == str(tmp_path / ".ddflow/driver_plugins.toml") for p in files)
