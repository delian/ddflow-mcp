"""DX-recover-brief-slice: the SessionStart output starts with the RESUME block
(folded B-res-brief-recovery-link): recoverable work comes before the digest, the
current item and the queue, so an agent that restarts after a crash acts on it first.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog

RESUME = "## ⚠ RESUME: recoverable work found"


def _seed(repo: Path, ids: list[str]) -> EventLog:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    for t in ids:
        log.append("task.added", t, {"parent": "P1", "title": t, "globs": [f"src/{t}.py"]})
    return log


def test_the_session_start_output_opens_with_the_resume_block(repo):
    log = _seed(repo, ["T1", "T9"])
    log.append("item.started", "T1", {})  # RUNNING with nobody on it: interrupted
    code, out, err = run_cli(repo, "hooks", "session-start")
    assert code == 0, err
    text = out + "\n" + err
    # The FIRST section the output names is the RESUME block -- nothing may preamble it
    # (duck #4): an interrupted session must see recoverable work in its first lines.
    first = re.search(r"^## ", text, re.M)
    assert first is not None, text
    assert text[first.start() :].startswith(RESUME), text
    assert text.index(RESUME) < text.index("# ddflow brief"), text
    assert text.index(RESUME) < text.index("## Suggested next"), text
    assert "**T1** (stale_running" in text, text


def test_the_brief_itself_puts_recoverable_work_before_new_work(repo):
    _seed(repo, ["T1", "T9"])
    EventLog(repo, "c").append("item.started", "T1", {})
    text = LC.brief(repo, item="T9", check_recovery=True, agent="next").data["text"]
    first = re.search(r"^## ", text, re.M)
    assert first is not None and text[first.start() :].startswith(RESUME), text
    assert text.index(RESUME) < text.index("## Suggested next: T9"), text
