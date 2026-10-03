"""An adopted orphan prompt/note appears once in the folded state and in SESSION.md
(bug B-adopted-orphan-session-md-dup). The id-less original stays in the append-only
log; the copy carrying `adopted_from` is the one that counts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _adopted_repo(repo):
    from ddflow.infra.log import EventLog

    run_cli(repo, "init")
    log = EventLog(repo, "old-agent")
    log.append("session.started", "s-early", {})
    log.append("session.prompt", "", {"text": "lost prompt", "item": ""})
    log.append("session.note", "", {"text": "lost note", "item": ""})
    code, _o, err = run_cli(repo, "session", "adopt-orphans")
    assert code == 0, err
    return log


def test_export_sessions_lists_an_adopted_orphan_once(repo):
    _adopted_repo(repo)
    code, out, err = run_cli(repo, "export", "sessions")
    assert code == 0, err
    assert out.count("lost prompt") == 1, out
    assert out.count("lost note") == 1, out


def test_fold_keeps_an_adopted_orphan_only_under_its_session(repo):
    from ddflow.core.model import fold

    log = _adopted_repo(repo)
    st = fold(log.read_all(), strict=False)
    texts = [p["text"] for s in st.sessions.values() for p in s.prompts]
    notes = [n["text"] for s in st.sessions.values() for n in s.notes]
    assert texts == ["lost prompt"] and notes == ["lost note"]
    assert "" not in st.sessions or not (st.sessions[""].prompts or st.sessions[""].notes)


def test_an_unadopted_orphan_is_still_folded(repo):
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog

    run_cli(repo, "init")
    log = EventLog(repo, "old-agent")
    log.append("session.prompt", "", {"text": "still lost", "item": ""})
    st = fold(log.read_all(), strict=False)
    assert [p["text"] for p in st.sessions[""].prompts] == ["still lost"]


def test_an_orphan_recorded_after_an_adoption_is_not_swallowed(repo):
    """Event ids are content hashes, not the (empty) subject: adopting one orphan must
    not hide another, whether older or newer."""
    from ddflow.core.model import fold

    log = _adopted_repo(repo)
    log.append("session.prompt", "", {"text": "later orphan", "item": ""})
    st = fold(log.read_all(), strict=False)
    assert [p["text"] for p in st.sessions[""].prompts] == ["later orphan"]
    assert [p["text"] for p in st.sessions["s-early"].prompts] == ["lost prompt"]
