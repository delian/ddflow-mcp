"""Bug B768503a43a: an entry under a companion's id counted as registered whatever it held.

`_registered_name` asked `servers.get(cid) is not None`, so `"codeguide": {}`, `"x"`,
`5` or `[]` read as "registered": `Status.usable` was True and `gate_coverage` counted the
gate as served while nothing could launch. `register()` meanwhile compared the entry with
the launch it would write, found them different and wrote -- the reader and the writer
disagreed about the same file. An id entry now counts only when it launches something
(a command) or names a remote server (a url).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import companions as CO


def _status(repo: Path, cid: str) -> CO.Status:
    return {st.companion.id: st for st in CO.scan(repo, probe=False)}[cid]


def _mcp_json(repo: Path, servers: dict) -> None:
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), "utf-8")


@pytest.mark.parametrize("junk", [{}, "x", 5, [], {"args": ["-y"]}, {"command": ""}])
def test_an_id_entry_that_launches_nothing_is_not_registered(tmp_path, junk):
    _mcp_json(tmp_path, {"context7": junk})
    st = _status(tmp_path, "context7")
    assert st.state != "registered", (junk, st.registered_in)
    assert st.usable is False and st.registered_in == []
    statuses = CO.scan(tmp_path, probe=False)
    assert "context7" not in CO.gate_coverage(tmp_path, statuses, ["research"])["research"]


def test_the_reader_agrees_with_the_writer_about_a_junk_entry(tmp_path):
    """`register` would write over the junk entry, so the reader must not call it done."""
    _mcp_json(tmp_path, {"context7": {}})
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    status, _msg = CO.register(tmp_path, c, "claude", dry_run=True)
    assert status == "written"
    assert _status(tmp_path, "context7").state != "registered"


def test_a_toml_table_under_the_id_that_launches_nothing_is_not_registered(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text("[mcp_servers.context7]\n", "utf-8")
    assert _status(tmp_path, "context7").registered_in == []


@pytest.mark.parametrize(
    "entry",
    [
        {"command": "npx", "args": ["-y", "some-wrapper-of-context7"]},
        {"command": ["npx", "-y", "x"]},
        {"url": "https://mcp.example.invalid/mcp"},
        {"type": "http", "url": "https://mcp.example.invalid/mcp"},
        {"httpUrl": "https://mcp.example.invalid/mcp"},
        {"serverUrl": "https://mcp.example.invalid/mcp"},
    ],
)
def test_an_id_entry_that_launches_or_names_a_server_still_counts(tmp_path, entry):
    """The operator's own launch (a wrapper, a remote) under the id is theirs to keep."""
    _mcp_json(tmp_path, {"context7": entry})
    assert _status(tmp_path, "context7").registered_as == {"claude": "context7"}


def test_a_toml_id_entry_with_a_launch_still_counts(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        '[mcp_servers.context7]\ncommand = "my-wrapper"\n', "utf-8"
    )
    assert _status(tmp_path, "context7").registered_as == {"codex": "context7"}


def _codex(repo: Path, text: str) -> None:
    (repo / ".codex").mkdir()
    (repo / ".codex" / "config.toml").write_text(text, "utf-8")


def test_the_toml_writer_agrees_with_the_reader_about_an_empty_table(tmp_path):
    """Reader: not registered. Writer: must not answer "already registers" (roborev 894)."""
    _codex(tmp_path, "[mcp_servers.context7]\n")
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    status, msg = CO.register(tmp_path, c, "codex")
    assert status == "refused" and "launches nothing" in msg, (status, msg)
    assert _status(tmp_path, "context7").registered_in == []


def test_an_unparseable_toml_file_registers_nothing(tmp_path):
    """The agent's own parser rejects the file, so nothing in it launches (roborev 894)."""
    _codex(tmp_path, '[mcp_servers.context7]\ncommand = "npx\n')
    st = _status(tmp_path, "context7")
    assert st.registered_in == [] and st.usable is False, st.registered_in
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    status, msg = CO.register(tmp_path, c, "codex")
    assert status == "refused" and "not valid TOML" in msg, (status, msg)


def test_a_sub_table_alone_under_the_id_still_lets_the_launch_be_added(tmp_path):
    """`[mcp_servers.<id>.env]` alone defines no launch; TOML lets the parent table follow."""
    _codex(tmp_path, '[mcp_servers.context7.env]\nFOO = "bar"\n')
    assert _status(tmp_path, "context7").registered_in == []
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    status, msg = CO.register(tmp_path, c, "codex")
    assert status == "written", msg
    assert _status(tmp_path, "context7").registered_as == {"codex": "context7"}


def test_a_junk_id_table_beside_a_real_launch_reads_as_registered_both_ways(tmp_path):
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    launch = c.entry()
    _codex(
        tmp_path,
        "[mcp_servers.context7]\n\n[mcp_servers.ctx]\n"
        f'command = "{launch["command"]}"\nargs = {json.dumps(launch["args"])}\n',
    )
    assert _status(tmp_path, "context7").registered_as == {"codex": "ctx"}
    status, msg = CO.register(tmp_path, c, "codex")
    assert status == "unchanged" and "`ctx`" in msg, (status, msg)


def test_nothing_is_appended_where_mcp_servers_is_not_a_table(tmp_path):
    _codex(tmp_path, "mcp_servers = 5\n")
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    status, msg = CO.register(tmp_path, c, "codex")
    assert status == "refused", (status, msg)
    assert (tmp_path / ".codex" / "config.toml").read_text() == "mcp_servers = 5\n"


def test_nothing_is_appended_where_mcp_servers_is_an_array_of_tables(tmp_path):
    """The append would parse, inside the last array element, where no agent reads it."""
    _codex(tmp_path, '[[mcp_servers]]\ncommand = "x"\n')
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    status, msg = CO.register(tmp_path, c, "codex")
    assert status == "refused" and "not a table" in msg, (status, msg)
    assert (tmp_path / ".codex" / "config.toml").read_text() == '[[mcp_servers]]\ncommand = "x"\n'


def test_an_inline_mcp_servers_table_is_refused_with_the_parser_s_reason(tmp_path):
    text = 'mcp_servers = { other = { command = "some-other-tool" } }\n'
    _codex(tmp_path, text)
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    status, msg = CO.register(tmp_path, c, "codex")
    assert status == "refused" and "not a table" not in msg and "would not parse" in msg, msg
    assert (tmp_path / ".codex" / "config.toml").read_text() == text


def test_a_different_launch_under_the_id_is_not_claimed_as_the_registry_s(tmp_path):
    _codex(tmp_path, '[mcp_servers.context7]\ncommand = "old-launcher"\n')
    c = {c.id: c for c in CO.load(tmp_path)}["context7"]
    status, msg = CO.register(tmp_path, c, "codex")
    assert status == "unchanged" and "already registers" not in msg, msg
