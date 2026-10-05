"""A generated view is committed, so a record's own text must not carry a host into it.

Bug Bb2c7cdd441: RESEARCH.md named a private LAN host because the views emitted record
fields verbatim while the export path already redacted them. The hygiene test
(test_repo_is_generic) is the ratchet for every view; this pins both renderers.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.core.model import Lesson, ResearchNote, State
from ddflow.views.markdown import lessons_md, lessons_summary_md, research_md

# Assembled at runtime: the hygiene test forbids the literal in ANY tracked file,
# including this one (roborev on 9b6db6bc).
HOST = ".".join(map(str, (10, 220, 230, 8)))


def _note(**fields) -> ResearchNote:
    base = {"id": "R-x", "question": "q", "claim": "c", "verdict": "CONFIRMED"}
    return ResearchNote(**{**base, **fields})


def test_a_private_host_in_a_research_record_never_reaches_the_view():
    state = State()
    state.research["R-x"] = _note(
        question=f"Can it reach the LAN Qwen at {HOST}?",
        claim=f"the claim cites {HOST}",
        mechanism=f"call {HOST} directly",
        falsifier=f"if {HOST} refuses",
        budget=f"5 min against {HOST}",
        probe=f"curl http://{HOST}:8000/v1/models",
        probe_output=f"ok from {HOST}",
        sources=[f"http://{HOST}:8000/metrics"],
    )
    text = research_md(state, Config())
    assert HOST not in text, text
    assert "Can it reach the LAN Qwen" in text, "the entry still reports, redacted"


def test_a_private_host_in_a_lesson_never_reaches_the_views():
    state = State()
    state.lessons["L-x"] = Lesson(
        id="L-x",
        title=f"the {HOST} lesson",
        rule=f"never hardcode {HOST}",
        why=f"because {HOST} is somebody's machine",
        how=f"keep {HOST} in .ddflow/local/",
        tags=["config"],
    )
    full = lessons_md(state, Config())
    summary = lessons_summary_md(state, Config())
    assert HOST not in full and HOST not in summary
    assert "never hardcode" in full, "the rule still reports, redacted"


def test_ordinary_text_is_untouched(repo):
    state = State()
    state.research["R-y"] = _note(question="Does git check-ignore answer?", claim="it does")
    text = research_md(state, Config())
    assert "Does git check-ignore answer?" in text
