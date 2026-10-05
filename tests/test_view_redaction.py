"""A generated view is committed, so a record's own text must not carry a host into it.

Bug Bb2c7cdd441: RESEARCH.md named a private LAN host because the view emitted record
fields verbatim while the export path already redacted them. The hygiene test
(test_repo_is_generic) is the ratchet for every view; this pins the renderer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.core.model import ResearchNote, State
from ddflow.views.markdown import research_md


def _note(**fields) -> ResearchNote:
    base = {"id": "R-x", "question": "q", "claim": "c", "verdict": "CONFIRMED"}
    return ResearchNote(**{**base, **fields})


def test_a_private_host_in_a_record_never_reaches_the_view():
    state = State()
    state.research["R-x"] = _note(
        question="Can it reach the LAN Qwen at 10.220.230.8?",
        probe="curl http://10.220.230.8:8000/v1/models",
        probe_output="ok from 10.220.230.8",
        mechanism="call 10.220.230.8 directly",
        sources=["http://10.220.230.8:8000/metrics"],
    )
    text = research_md(state, Config())
    assert "10.220.230.8" not in text, text
    assert "R-x" in text and "CONFIRMED" in text, "the entry still reports, redacted"


def test_ordinary_text_is_untouched(repo):
    state = State()
    state.research["R-y"] = _note(question="Does git check-ignore answer?", claim="it does")
    text = research_md(state, Config())
    assert "Does git check-ignore answer?" in text and "[redacted]" not in text
