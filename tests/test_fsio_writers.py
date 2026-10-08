"""The writers that moved onto fsio (B-uni-fsio-writers): what each leaves on disk is what
`write_text` / the hand-rolled temp-file writers left, and none leaves a temp file behind.

The case that matters is a symlinked target: `CLAUDE.md -> AGENTS.md`, a dotfiles-managed
`.mcp.json` or a linked git hook is written THROUGH, never replaced by a regular file.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from ddflow.services import adopt as A
from ddflow.services import enforce as E
from ddflow.services import flowstate as FS


def _tmp_leftovers(d: Path) -> list[str]:
    return sorted(p.name for p in d.rglob("*.tmp"))


def test_the_managed_block_is_written_through_a_symlinked_rulebook(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Project\n\nown text\n")
    os.symlink("AGENTS.md", tmp_path / "CLAUDE.md")
    section = A.BLOCK.render("ddflow block\n").strip()
    assert A._upsert_block(tmp_path / "CLAUDE.md", section) == "appended to CLAUDE.md"
    assert (tmp_path / "CLAUDE.md").is_symlink()
    assert (tmp_path / "AGENTS.md").read_text() == f"# Project\n\nown text\n\n{section}\n"
    assert A._upsert_block(tmp_path / "CLAUDE.md", A.BLOCK.render("new block\n").strip()) == (
        "updated the managed block in CLAUDE.md"
    )
    assert "new block" in (tmp_path / "AGENTS.md").read_text()
    assert (tmp_path / "CLAUDE.md").is_symlink()
    assert _tmp_leftovers(tmp_path) == []


def test_the_mcp_config_is_merged_through_a_symlink(repo, monkeypatch):
    real = repo / "dotfiles-mcp.json"
    real.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    os.symlink(real.name, repo / ".mcp.json")
    monkeypatch.setattr(A, "_launch_entry", lambda *a, **k: {"command": "ddflow", "args": []})
    assert A._register_mcp(repo, "claude") == "registered ddflow in .mcp.json"
    assert (repo / ".mcp.json").is_symlink()
    assert set(json.loads(real.read_text())["mcpServers"]) == {"other", "ddflow"}
    assert _tmp_leftovers(repo) == []


def test_a_ddflow_hook_is_rewritten_through_a_link_and_stays_executable(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    impl = tmp_path / "pre-commit.impl"
    impl.write_text(f"#!/bin/sh\n{E.HOOK_MARKER}\nold\n")
    impl.chmod(0o755)
    os.symlink(impl, hooks / "pre-commit")
    msg = E._install_one(
        tmp_path, hooks, "pre-commit", "#!/bin/sh\n{marker}\n{invocation}\n", "run", False
    )
    assert msg.startswith("updated the ddflow pre-commit hook")
    assert (hooks / "pre-commit").is_symlink()
    written = impl.read_text()
    assert written.startswith("#!/bin/sh\n# ddflow:begin hooks/pre-commit ddflow=")
    assert f"\n{E.HOOK_MARKER}\nrun\n# ddflow:end hooks/pre-commit\n" in written
    assert impl.stat().st_mode & stat.S_IXUSR
    assert _tmp_leftovers(tmp_path) == []


def test_the_flow_ring_rewrite_keeps_its_mode_and_leaves_no_temp(tmp_path):
    ring = tmp_path / "flow.jsonl"
    ring.write_text('{"at": 1}\n')
    ring.chmod(0o640)
    FS._replace(ring, '{"at": 2}\n')
    assert ring.read_text() == '{"at": 2}\n'
    assert stat.S_IMODE(ring.stat().st_mode) == 0o640
    FS._replace(tmp_path / "fresh.jsonl", "x\n")
    assert stat.S_IMODE((tmp_path / "fresh.jsonl").stat().st_mode) == 0o644
    assert _tmp_leftovers(tmp_path) == []


_RULE_ROUND_TRIP = """
import sys
from pathlib import Path
from ddflow.services.rules import Rule, RulesStorage
s = RulesStorage(Path(sys.argv[1]))
s.add(Rule(id="r-cafe", title="Caf\\u00e9 rule", content="na\\u00efve \\u2014 text"))
print(s.get("r-cafe").content == "na\\u00efve \\u2014 text")
"""


def test_a_non_ascii_rule_round_trips_under_an_ascii_locale(tmp_path):
    """Bd5adf89e33: rule files were written and read in the locale encoding, so a rule
    with a non-ASCII character failed under an ASCII locale (and was not UTF-8 on a
    cp1252 machine). They are UTF-8 both ways now."""
    env = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
    }
    env.pop("PYTHONIOENCODING", None)
    r = subprocess.run(
        [sys.executable, "-c", _RULE_ROUND_TRIP, str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "True"
    raw = (tmp_path / ".ddflow" / "rules" / "r-cafe.toml").read_bytes()
    assert "naïve".encode() in raw
