"""The handshake and the SessionStart brief word companion advice from ONE helper.
(B-companion-actionable-shared)"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _brief_words(repo) -> dict[str, str]:
    from ddflow.api.setup import _companion_lines

    out = {}
    for ln in _companion_lines(repo):
        if ln.startswith("- **"):
            cid = ln.split("**")[1]
            out[cid] = ln.split("[", 1)[1].split("]", 1)[0]
    return out


def _handshake_words(repo) -> dict[str, str]:
    from ddflow.surfaces.mcp import _instruction_vars

    return {c["id"]: c["state_word"] for c in _instruction_vars(repo)["actionable_companions"]}


def test_both_surfaces_render_the_same_words(repo):
    run_cli(repo, "init")
    brief, hand = _brief_words(repo), _handshake_words(repo)
    assert brief and brief == hand
    assert set(brief.values()) <= {"installed, not registered", "not installed", "not checked"}


def test_shared_helper_words_and_membership(repo):
    from ddflow.services import companions as CO

    run_cli(repo, "init")
    sts = CO.scan(repo, probe=False)
    got = CO.actionable(sts)
    assert [(s.companion.id, w) for s, w in got] == [
        (s.companion.id, CO.ADVICE_WORDS[s.advice])
        for s in sts
        if s.companion.default and s.advice in CO.ADVICE_WORDS
    ]
    assert all(s.companion.default and s.advice != "ok" for s, _ in got)
    assert CO.ADVICE_WORDS == {
        "register": "installed, not registered",
        "install": "not installed",
        "check": "not checked",
    }
