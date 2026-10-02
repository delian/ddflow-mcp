"""Bug B662a1ace82: `companions add` never refreshed a stale TOML entry under the id.

The JSON path rewrites an entry that is not the registry's launch; the TOML path answered
"already registers" over `.codex/config.toml` holding an old launch line and left it.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import companions as CO


def _codex(repo: Path, text: str) -> Path:
    (repo / ".codex").mkdir()
    p = repo / ".codex" / "config.toml"
    p.write_text(text, "utf-8")
    return p


def _c(repo: Path) -> CO.Companion:
    return {c.id: c for c in CO.load(repo)}["context7"]


def _servers(p: Path) -> dict:
    return tomllib.loads(p.read_text("utf-8"))["mcp_servers"]


def test_a_stale_toml_launch_under_the_id_is_refreshed(tmp_path):
    p = _codex(
        tmp_path,
        'model = "x"\n\n[mcp_servers.other]\ncommand = "keep-me"\n\n'
        '[mcp_servers.context7]\ncommand = "old-launcher"\nargs = ["--old"]\n\n'
        '[mcp_servers.context7.env]\nOLD = "1"\n\n[mcp_servers.zzz]\ncommand = "also-keep"\n',
    )
    c = _c(tmp_path)
    status, msg = CO.register(tmp_path, c, "codex")
    assert status == "written", msg
    srv = _servers(p)
    assert srv["context7"]["command"] == c.command and srv["context7"]["args"] == list(c.args)
    assert "env" not in srv["context7"], "the old sub-table went with the old entry"
    assert srv["other"] == {"command": "keep-me"} and srv["zzz"] == {"command": "also-keep"}
    assert tomllib.loads(p.read_text("utf-8"))["model"] == "x"
    assert CO.register(tmp_path, c, "codex")[0] == "unchanged"


def test_the_dry_run_previews_the_refresh_and_touches_nothing(tmp_path):
    text = '[mcp_servers.context7]\ncommand = "old-launcher"\n'
    p = _codex(tmp_path, text)
    status, msg = CO.register(tmp_path, _c(tmp_path), "codex", dry_run=True)
    assert status == "written" and "old-launcher" not in msg and _c(tmp_path).command in msg
    assert p.read_text("utf-8") == text


def test_a_stale_entry_that_cannot_be_cut_out_is_refused_not_mangled(tmp_path):
    text = 'mcp_servers.context7 = { command = "old-launcher" }\n'
    p = _codex(tmp_path, text)
    status, msg = CO.register(tmp_path, _c(tmp_path), "codex")
    assert status == "refused" and "by hand" in msg, (status, msg)
    assert p.read_text("utf-8") == text


def test_the_same_launch_elsewhere_is_not_doubled_by_the_refresh(tmp_path):
    c = _c(tmp_path)
    p = _codex(
        tmp_path,
        '[mcp_servers.context7]\ncommand = "old-launcher"\n\n[mcp_servers.ctx]\n'
        f'command = "{c.command}"\nargs = {list(c.args)!r}\n'.replace("'", '"'),
    )
    before = p.read_text("utf-8")
    status, msg = CO.register(tmp_path, c, "codex")
    assert status == "unchanged" and "`ctx`" in msg, (status, msg)
    assert p.read_text("utf-8") == before


def test_junk_entries_keep_their_refusals(tmp_path):
    p = _codex(tmp_path, "[mcp_servers.context7]\n")
    status, msg = CO.register(tmp_path, _c(tmp_path), "codex")
    assert status == "refused" and "launches nothing" in msg
    assert p.read_text("utf-8") == "[mcp_servers.context7]\n"


def test_a_comment_above_the_next_table_survives_the_refresh(tmp_path):
    p = _codex(
        tmp_path,
        '[mcp_servers.context7]\ncommand = "old-launcher"\n\n# the other one, keep me\n'
        '[mcp_servers.other]\ncommand = "keep-me"\n',
    )
    assert CO.register(tmp_path, _c(tmp_path), "codex")[0] == "written"
    assert "# the other one, keep me\n[mcp_servers.other]" in p.read_text("utf-8")
