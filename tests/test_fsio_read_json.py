"""fsio.read_json: one reader for the JSON configs other tools also write (B-uni-fsio-writers
.2-read-json), and each caller's answer for each kind of bad file pinned as it was --
except the invalid-UTF-8 file that crashed adopt and companions add (B26e804cd45).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ddflow.infra import fsio
from ddflow.services import adopt as A
from ddflow.services import claudehooks as CH
from ddflow.services import companions as CO
from ddflow.services import harness as H
from ddflow.services import launchers as LA

BAD = {
    "invalid": b"{",
    "unreadable": b"\xff\xfe{",
    "not-object": b"[1, 2]",
}


@pytest.mark.parametrize(
    ("raw", "want"),
    [(None, {}), (b"", {}), (b'{"a": 1}', {"a": 1})],
)
def test_a_missing_empty_or_object_file_is_a_dict(tmp_path, raw, want):
    if raw is not None:
        (tmp_path / "c.json").write_bytes(raw)
    assert fsio.read_json(tmp_path / "c.json") == want


@pytest.mark.parametrize("kind", sorted(BAD))
def test_anything_else_is_unreadable_never_an_exception(tmp_path, kind):
    (tmp_path / "c.json").write_bytes(BAD[kind])
    got = fsio.read_json(tmp_path / "c.json")
    assert isinstance(got, fsio.Unreadable) and got.kind == kind
    assert got.path == tmp_path / "c.json"
    assert (got.value == [1, 2]) if kind == "not-object" else got.detail


def test_a_directory_is_unreadable(tmp_path):
    (tmp_path / "c.json").mkdir()
    got = fsio.read_json(tmp_path / "c.json")
    assert isinstance(got, fsio.Unreadable) and got.kind == "unreadable"


# -- each caller says what it said before ----------------------------------------------


@pytest.mark.parametrize(
    ("kind", "said"),
    [
        ("invalid", "is not valid JSON (Expecting property name enclosed in double quotes"),
        ("unreadable", "could not be read ('utf-8' codec can't decode byte 0xff"),
        ("not-object", "is not a JSON object; not touching it"),
    ],
)
def test_claudehooks_refuses_each_bad_settings_file_in_its_own_words(tmp_path, kind, said):
    path = tmp_path / "settings.json"
    path.write_bytes(BAD[kind])
    with pytest.raises(CH.SettingsError) as e:
        CH._read(path)
    assert str(e.value).startswith(f"{path} {said}")
    assert CH._read(tmp_path / "missing.json") == {}


@pytest.mark.parametrize(
    ("kind", "said"),
    [
        ("invalid", ".mcp.json could not be read (Expecting property name"),
        ("unreadable", ".mcp.json could not be read ('utf-8' codec can't decode byte 0xff"),
        ("not-object", ".mcp.json is not a JSON object; fix it by hand"),
    ],
)
def test_harness_refuses_each_bad_mcp_file_in_its_own_words(tmp_path, kind, said):
    (tmp_path / ".mcp.json").write_bytes(BAD[kind])
    with pytest.raises(H.HarnessError) as e:
        H.project_servers(tmp_path)
    assert str(e.value).startswith(said)
    assert H.project_servers(tmp_path / "nowhere") == {}


@pytest.mark.parametrize("kind", sorted(BAD))
def test_the_launcher_checks_skip_a_bad_file(tmp_path, kind):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_bytes(BAD[kind])
    (tmp_path / ".mcp.json").write_bytes(BAD[kind])
    assert LA.check_settings(tmp_path) == []
    assert LA.check_mcp(tmp_path) == []


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


@pytest.mark.parametrize(
    ("kind", "said"),
    [
        ("invalid", "SKIPPED .mcp.json: it is not valid JSON; add the server by hand"),
        ("unreadable", "SKIPPED .mcp.json: it could not be read ('utf-8' codec can't decode"),
        ("not-object", "SKIPPED .mcp.json: not a JSON object where 'mcpServers' servers belong"),
    ],
)
def test_adopt_refuses_each_bad_mcp_file(git_repo, monkeypatch, kind, said):
    """'unreadable' is B26e804cd45: an invalid-UTF-8 .mcp.json crashed adopt with a
    UnicodeDecodeError instead of being refused like an invalid one."""
    (git_repo / ".mcp.json").write_bytes(BAD[kind])
    monkeypatch.setattr(A, "_launch_entry", lambda *a, **k: {"command": "ddflow", "args": []})
    out = A._register_mcp(git_repo, "claude")
    assert isinstance(out, A.Refused) and str(out).startswith(said), out
    assert (git_repo / ".mcp.json").read_bytes() == BAD[kind]


@pytest.mark.parametrize(
    ("kind", "said"),
    [
        ("invalid", "SKIPPED .mcp.json: it is not valid JSON; add context7 by hand"),
        ("unreadable", "SKIPPED .mcp.json: it could not be read ('utf-8' codec can't decode"),
    ],
)
def test_companions_add_refuses_a_bad_mcp_file(tmp_path, kind, said):
    """'unreadable' is B26e804cd45 for `ddflow companions add`."""
    (tmp_path / ".mcp.json").write_bytes(BAD[kind])
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    for dry_run in (True, False):
        status, msg = CO.register(tmp_path, c, "claude", dry_run=dry_run)
        assert status == "refused" and msg.startswith(said), msg
    assert (tmp_path / ".mcp.json").read_bytes() == BAD[kind]


def test_companions_add_answers_a_non_object_file_as_before(tmp_path):
    """A list where the server map belongs goes on to the placement, which refuses it in
    its own words, as it always did."""
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    (tmp_path / ".mcp.json").write_text(json.dumps([1, 2]))
    for dry_run in (True, False):
        assert CO.register(tmp_path, c, "claude", dry_run=dry_run) == (
            "refused",
            "SKIPPED .mcp.json: not a JSON object where 'mcpServers' servers belong; "
            "add context7 by hand",
        )


@pytest.mark.parametrize("rel", [".mcp.json", ".codex/config.toml"])
def test_companions_status_reads_an_undecodable_config_as_unreadable(tmp_path, rel):
    """B8bd68c2e6c: `scan` (companions status) raised UnicodeDecodeError on it."""
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_bytes(BAD["unreadable"])
    statuses = {s.companion.id: s for s in CO.scan(tmp_path, probe=False)}
    assert statuses["context7"].registered_in == []


def test_the_toml_mcp_config_is_refused_when_undecodable_too(git_repo, monkeypatch):
    """B26e804cd45, the TOML (codex) half of adopt and companions add."""
    rel = ".codex/config.toml"
    (git_repo / ".codex").mkdir()
    (git_repo / rel).write_bytes(BAD["unreadable"])
    monkeypatch.setattr(A, "_launch_entry", lambda *a, **k: {"command": "ddflow", "args": []})
    out = A._register_mcp(git_repo, "codex")
    assert isinstance(out, A.Refused)
    assert str(out).startswith(f"SKIPPED {rel}: it could not be read ('utf-8' codec")
    c = {c.id: c for c in CO.load(git_repo)}["context7"]
    status, msg = CO.register(git_repo, c, "codex")
    assert status == "refused" and msg.startswith(f"SKIPPED {rel}: it could not be read ("), msg
    assert (git_repo / rel).read_bytes() == BAD["unreadable"]
