"""The driver-plugin SDK: registration is operator-only, a plugin conforms before it loads.

Third-party drivers (operator decision D-DRV-Q1, option C) register as an in-process
``entry_point`` (an object that builds a ``Lane``) or an external ``executable`` speaking a
versioned JSON-over-stdio protocol. Registration is read only from the operator's own directory
(the user ddflow dir, outside the repository), so a project layer cannot register one. Nothing
enables before its registration-time conformance, and an unknown protocol version is refused;
every test here is offline (no spawned process, no installed agent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.adapters.lanes import Lane
from ddflow.services import driverplugins as DP


def _write_plugin(user_home: Path, text: str) -> None:
    """Write ``text`` as the operator (user) registration file the SDK reads."""
    path = DP.registration_files(user_home)[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def test_an_unregistered_plugin_never_loads(tmp_path: Path):
    with pytest.raises(DP.PluginError):
        DP.load("no-such-plugin", user_home=tmp_path / "home")


def test_a_project_layer_cannot_register_a_plugin(tmp_path: Path):
    # A project-layer file (inside the repository) is not the user directory, so it is not read;
    # the fixture is a VALID executable (protocol 1) so absence cannot be met by malformed-input
    # rejection — it must be absent because that layer is not an operator layer.
    project = tmp_path / ".ddflow" / "driver_plugins.toml"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        '[[plugin]]\nname = "sneaky"\nkind = "executable"\nprotocol = 1\nargv = ["/bin/true"]\n'
    )
    assert "sneaky" not in DP.discover_plugins(user_home=tmp_path / "home")
    assert list(DP.registration_files(tmp_path / "home")) == [
        tmp_path / "home" / "driver_plugins.toml"
    ]
    with pytest.raises(DP.PluginError):
        DP.load("sneaky", user_home=tmp_path / "home")


def test_a_plugin_failing_conformance_is_refused(tmp_path: Path):
    # ddflow.adapters.env:SystemEnv is a real ddflow object but not a Lane, so it does not conform.
    _write_plugin(
        tmp_path / "home",
        '[[plugin]]\nname = "bad"\nkind = "entry_point"\nentry_point = "ddflow.adapters.env:SystemEnv"\n',
    )
    with pytest.raises(DP.PluginError):
        DP.load("bad", user_home=tmp_path / "home")


def test_an_unknown_protocol_version_is_refused(tmp_path: Path):
    _write_plugin(
        tmp_path / "home",
        '[[plugin]]\nname = "future"\nkind = "executable"\nprotocol = 99\nargv = ["future-driver"]\n',
    )
    with pytest.raises(DP.PluginError):
        DP.discover_plugins(tmp_path / "home")


def test_a_conforming_entry_point_builds_a_lane(tmp_path: Path):
    _write_plugin(
        tmp_path / "home",
        '[[plugin]]\nname = "fake"\nkind = "entry_point"\ntrust = "operator"\nentry_point = "ddflow.adapters.fakes.lanes:FakeLane"\n',
    )
    lane = DP.load("fake", user_home=tmp_path / "home")
    assert isinstance(lane, Lane)


def test_an_unknown_key_is_refused_not_silently_dropped(tmp_path: Path):
    # A typo like `sandox` would silently drop the env allowlist; an unknown key is refused. The
    # entry is otherwise a VALID executable, so the unknown key is the only invalid thing in it.
    _write_plugin(
        tmp_path / "home",
        '[[plugin]]\nname = "x"\nkind = "executable"\nprotocol = 1\nsandox = ["GEMINI_API_KEY"]\nargv = ["/bin/true"]\n',
    )
    with pytest.raises(DP.PluginError):
        DP.discover_plugins(tmp_path / "home")


def test_an_unknown_top_level_key_is_refused(tmp_path: Path):
    # A stray top-level key (anything but ``plugin``) was silently dropped: a mis-indented
    # ``name = "..."`` sat beside the ``[[plugin]]`` tables and read as "no plugins". Refused.
    _write_plugin(
        tmp_path / "home",
        'name = "oops"\n[[plugin]]\nname = "x"\nkind = "executable"\nprotocol = 1\nargv = ["/bin/true"]\n',
    )
    with pytest.raises(DP.PluginError):
        DP.discover_plugins(tmp_path / "home")
