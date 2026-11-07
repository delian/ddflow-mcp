"""Driver packs: layered descriptor/model extension and the operator-only split.

D-DRV-5 / D-DRV-Q5. A cloned project may add detection, paths and facts but never a key
that runs a process; a company pack is data only and pinned by digest (a bare URL without
a digest is refused); each file carries a schema version.
"""

from __future__ import annotations

import hashlib

import pytest

from ddflow.services import driverpacks as DP
from ddflow.services import harnessreg

#: A minimal descriptor, valid on its own (an agent ddflow ships nothing for).
_GOOD = """
id = "demo"
rank = 99
family = "demo"
delta = "demo.md"

[mcp]
path = ".demo/mcp.json"
shape = "mcpServers"

[verified]
date = "2026-10-09"
sources = ["https://example.test/docs"]
"""


def _project(root, *files: tuple[str, str]) -> None:
    d = root / ".ddflow" / "harnesses.d"
    d.mkdir(parents=True, exist_ok=True)
    for name, text in files:
        (d / name).write_text(text)


# -- schema ----------------------------------------------------------------


def test_schema_of_reads_the_declared_version():
    assert DP.schema_of("schema = 2\n" + _GOOD) == 2


def test_schema_of_is_none_when_absent():
    assert DP.schema_of(_GOOD) is None


def test_require_known_schema_allows_the_current_schema():
    DP.require_known_schema("schema = 2\n" + _GOOD)


def test_require_known_schema_refuses_a_newer_schema():
    with pytest.raises(DP.PackError, match="unavailable: upgrade ddflow"):
        DP.require_known_schema("schema = 99\n" + _GOOD)


# -- operator-only split ---------------------------------------------------


def test_headless_argv_is_operator_only():
    raw = {"headless": {"argv": ["claude", "-p"]}}
    assert DP.operator_only_violations(raw) == ["headless.argv"]


def test_probe_is_operator_only():
    raw = {"probe": [["claude", "--version"]]}
    assert DP.operator_only_violations(raw) == ["probe"]


def test_non_process_keys_are_not_operator_only():
    raw = {"headless": {"model_flag": "-m"}, "detect": {"client_names": ["thing"]}}
    assert DP.operator_only_violations(raw) == []


def test_a_project_descriptor_may_add_an_agent(tmp_path):
    _project(tmp_path, ("demo.toml", _GOOD))
    assert "demo" in {h.id for h in DP.load_descriptors(tmp_path)}


def test_a_project_descriptor_may_not_add_launch_argv(tmp_path):
    launching = _GOOD + '[headless]\nautonomy = "basic"\nargv = ["claude", "-p"]\n'
    _project(tmp_path, ("claude.toml", launching.replace('id = "demo"', 'id = "claude"')))
    with pytest.raises(DP.PackError, match=r"headless\.argv"):
        DP.load_descriptors(tmp_path)


def test_a_project_descriptor_may_not_add_probe_argv(tmp_path):
    probing = _GOOD + "probe = [['toy', '--version']]\n"
    _project(tmp_path, ("demo.toml", probing))
    with pytest.raises(DP.PackError, match="probe"):
        DP.load_descriptors(tmp_path)


# -- layers ----------------------------------------------------------------


def test_layers_merge_with_later_winning(tmp_path):
    user = tmp_path / "user"
    (user / "harnesses.d").mkdir(parents=True)
    (user / "harnesses.d" / "demo.toml").write_text(
        _GOOD.replace('family = "demo"', 'family = "userfam"')
    )
    _project(tmp_path, ("demo.toml", _GOOD.replace('family = "demo"', 'family = "projfam"')))
    by_id = {h.id: h for h in DP.load_descriptors(tmp_path, user_home=user)}
    assert by_id["demo"].family == "projfam", "project wins over user"


def test_shipped_descriptors_load_with_no_extra_layers(tmp_path):
    assert DP.load_descriptors(tmp_path) == harnessreg.load()


# -- packs -----------------------------------------------------------------


def test_a_bare_url_pack_without_digest_is_refused():
    with pytest.raises(DP.PackError, match="bare URL pack without a digest is refused"):
        DP.load_pack("https://example.test/pack.toml")


def test_a_local_pack_matching_its_digest_loads(tmp_path):
    p = tmp_path / "pack.toml"
    p.write_text(_GOOD)
    assert DP.load_pack(str(p), digest=DP.content_digest(_GOOD))["id"] == "demo"


def test_a_local_pack_with_a_mismatched_digest_is_refused(tmp_path):
    p = tmp_path / "pack.toml"
    p.write_text(_GOOD)
    with pytest.raises(DP.PackError, match="digest mismatch"):
        DP.load_pack(str(p), digest=hashlib.sha256(b"other").hexdigest())


def test_content_digest_is_sha256_hex():
    assert DP.content_digest(b"x") == hashlib.sha256(b"x").hexdigest()


def test_descriptor_dirs_names_all_four_layers(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    by_name = dict(DP.descriptor_dirs(tmp_path))
    assert set(by_name) == {"packaged", "user", "project", "local"}
    assert by_name["project"] == tmp_path / ".ddflow" / "harnesses.d"
    assert by_name["local"] == tmp_path / ".ddflow" / "local" / "harnesses.d"
