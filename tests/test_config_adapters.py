"""Acceptance for the `[adapters]` knob register (ADP-knobs): every knob is declared once,
has its default, and is read back through `ddflow.services.adapters` -- the code path that
uses it -- when set by the project file and when checked.

The five write routes (`.ddflow/config.toml` and `conf.d`, the local file, `ddflow config
KEY VALUE`, `ddflow_configure`, and the prompt command) and the org-bundle lock are the
config engine's own contract, proved once for every knob by `KNOB-policy-acceptance`;
here one route is exercised end to end so a name or coercion break is caught beside the
declaration, not deferred.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.config_sections._docs import DECLARED
from ddflow.services import adapters as services_adapters


def _cfg_with_adapters(tmp_path: Path, body: str) -> Config:
    (tmp_path / ".ddflow").mkdir(exist_ok=True)
    (tmp_path / ".ddflow" / "config.toml").write_text(body, encoding="utf-8")
    return Config.load(tmp_path)


def test_every_adapters_knob_is_declared_once():
    for knob in (
        "platform",
        "lane_transport",
        "network_consent",
        "managed_files_consent",
        "managed_files_backup",
    ):
        assert f"adapters.{knob}" in DECLARED, f"adapters.{knob} is not in the knob registry"
    # a knob declared twice would have raised at import; the ratchet does the heavy lifting.


def test_defaults_are_auto_local_empty_and_backup_on():
    a = Config().adapters
    assert a.platform == "auto"
    assert a.lane_transport == "local"
    assert dict(a.network_consent) == {}
    assert dict(a.managed_files_consent) == {}
    assert a.managed_files_backup is True


def test_platform_selector_reads_back_through_services_adapters(tmp_path: Path):
    cfg = _cfg_with_adapters(tmp_path, '[adapters]\nplatform = "posix"\n')
    assert type(services_adapters.selected_platform(cfg)).__name__ == "Posix"
    auto = _cfg_with_adapters(tmp_path, '[adapters]\nplatform = "auto"\n')
    assert type(services_adapters.selected_platform(auto)).__name__ == "Posix"  # this host is POSIX


def test_platform_unknown_is_refused_with_the_list(tmp_path: Path):
    cfg = _cfg_with_adapters(tmp_path, '[adapters]\nplatform = "windows"\n')
    # a FILE may carry an unknown value (lenient): it falls back to the strictest, 'posix'.
    assert cfg.adapters.platform == "posix"
    # the write/check path refuses it outright.
    with pytest.raises(ValueError):
        Config.check({"adapters": {"platform": "windows"}})


def test_lane_transport_reads_back_a_registered_adapter(tmp_path: Path):
    cfg = _cfg_with_adapters(tmp_path, '[adapters]\nlane_transport = "local"\n')
    assert services_adapters.selected_lane_transport(cfg).__name__ == "LocalTransport"


def test_network_consent_reads_back_per_kind_and_absent_is_declined(tmp_path: Path):
    cfg = _cfg_with_adapters(
        tmp_path,
        "[adapters]\n[adapters.network_consent]\nforge = true\nchat = true\n",
    )
    assert services_adapters.network_consented(cfg, "forge") is True
    assert services_adapters.network_consented(cfg, "chat") is True
    assert services_adapters.network_consented(cfg, "webhook") is False  # absent is declined
    assert services_adapters.network_consented(cfg, "email") is False


def test_managed_files_consent_reads_back_per_kind(tmp_path: Path):
    cfg = _cfg_with_adapters(
        tmp_path, '[adapters]\nmanaged_files_consent = { "settings.json" = true }\n'
    )
    assert services_adapters.managed_files_consented(cfg, "settings.json") is True
    assert services_adapters.managed_files_consented(cfg, ".mcp.json") is False  # absent is refused


def test_managed_files_backup_reads_back_and_defaults_on(tmp_path: Path):
    cfg = _cfg_with_adapters(tmp_path, "[adapters]\nmanaged_files_backup = false\n")
    assert services_adapters.managed_files_backup(cfg) is False
    assert services_adapters.managed_files_backup(Config()) is True


def test_adapters_section_fields_match_the_declared_knobs():
    """The section and the service a caller reaches it through stay in step (a renamed field
    breaks the reader the ratchet found by name)."""
    fields = {f.name for f in dataclasses.fields(Config().adapters)}
    assert fields == {
        "platform",
        "lane_transport",
        "network_consent",
        "managed_files_consent",
        "managed_files_backup",
    }
