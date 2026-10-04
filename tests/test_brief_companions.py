"""Companions reach every agent: the SessionStart brief names missing ones, README names
the install-companions command. (B-companion-gaps)"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

README = Path(__file__).resolve().parents[1] / "README.md"


def test_session_start_brief_lists_missing_default_companions(repo):
    run_cli(repo, "init")
    code, out, _err = run_cli(repo, "hooks", "session-start")
    assert code == 0
    assert "Companions not wired up" in out
    assert "install-companions" in out
    # Nothing was probed at session start, and the section says so.
    assert "not checked" in out
    # Each default companion is named with its install command.
    from ddflow.services import companions as CO

    gaps = [s for s in CO.scan(repo, probe=False) if s.companion.default and s.advice != "ok"]
    assert gaps
    for st in gaps:
        assert st.companion.id in out


def test_readme_names_install_companions_and_counts_right():
    text = README.read_text()
    start = text.index("### Companion tools")
    section = text[start : text.index("\n### ", start + 5)]
    assert "install-companions" in section
    assert "Adding a fifth is a TOML block" not in text
