"""The human-renderer registry, which `core/outcome.py` has always promised.

`Outcome`'s docstring says a kind with no renderer "is a result that cannot be shown to a
person, and `tests/test_views.py` refuses to let one exist". Both that module and this
file were named in prose long before either existed — found while migrating B37, and
fixed by writing them rather than by softening the sentence.

The invariant is scoped to what it can honestly hold: every tool that declares its body
is TEXT must have a renderer reachable for its kind. Tools returning JSON need none, and
asserting otherwise would be a registry of empty functions.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from ddflow.core import outcome as O
from ddflow.views import human


def test_the_registry_is_not_empty():
    """A registry with nothing in it passes every other test in this file."""
    assert human.RENDERERS, "views/human.py registers no renderer, so it is decoration"


def test_the_registry_is_reachable_by_kind():
    """`render(out)` dispatches on `Outcome.kind`, which is the contract `core/outcome.py`
    states: "`kind` selects the renderer in `views.human`; it is not free text".

    Deliberately NOT "every text-bodied tool has a renderer here" — that was the first
    version and it was the wrong invariant. `board` and `render` produce their documents
    in `views/markdown.py` and `replay` in `services/sessions.py`; all three are already
    below both surfaces, so requiring a second renderer here would mean either a
    duplicate or a pass-through. What this module owns is the rendering that had NOWHERE
    else to live. `tests/test_api_layer.py::test_every_text_bodied_tool_actually_returns_a
    _STRING` is the check that covers all of them.
    """
    for kind, fn in human.RENDERERS.items():
        assert human.render(O.ok(kind, **_MINIMAL[kind])) == fn(O.ok(kind, **_MINIMAL[kind]))


#: The smallest data each registered kind needs. A new renderer must add a row, which is
#: the point: a renderer nobody can call with anything is untested by construction.
_MINIMAL: dict[str, dict] = {
    "setup": {"actions": [], "agents": ["claude"], "companions_ready": [], "companions_absent": []},
    "config": {"rows": [], "explain": False, "path": ""},
    "reviewers.list": {"reviewers": [], "unclassified": []},
    "reviewers.detect": {"found": [], "written": "", "blocks": "", "count": 0},
    "doctor": {
        "problems": [],
        "notes": [],
        "events": 0,
        "items": 0,
        "agent": "a",
        "repo": "/r",
        "index_stale": False,
        "fts": False,
    },
}


def test_every_registered_kind_has_a_minimal_case():
    missing = sorted(set(human.RENDERERS) - set(_MINIMAL))
    assert not missing, f"renderers with no minimal case: {missing}"


def test_an_unrenderable_kind_says_so_rather_than_returning_nothing():
    """The failure mode matters. A missing renderer returning "" would present as a
    healthy empty report, which for `doctor` reads as "nothing is wrong"."""
    with pytest.raises(KeyError, match="no human renderer"):
        human.render(O.ok("a-kind-nobody-renders"))


def test_two_renderers_cannot_claim_one_kind():
    """Otherwise the second silently wins and the first becomes dead code that reads as
    live."""
    with pytest.raises(RuntimeError, match="two renderers claim"):

        @human.renders("doctor")
        def _rival(out):
            return ""


def test_doctor_puts_notes_before_problems():
    """A note is CONTEXT for the problems that follow — a stale index, an unreachable
    reviewer. Printing problems first means the note explaining one arrives after it."""
    text = human.doctor(
        O.ok(
            "doctor",
            problems=["dependency cycle: A -> B -> A"],
            notes=["index is stale"],
            events=12,
            items=3,
            agent="alpha",
            repo="/tmp/proj",
            index_stale=True,
            fts=True,
        )
    )
    assert text.index("note: index is stale") < text.index("PROBLEM: dependency cycle")
    assert "1 problem(s)." in text
    assert "stale (auto-rebuilds)" in text


def test_a_healthy_doctor_report_says_so():
    text = human.doctor(
        O.ok(
            "doctor",
            problems=[],
            notes=[],
            events=1,
            items=0,
            agent="a",
            repo="/r",
            index_stale=False,
            fts=False,
        )
    )
    assert "Healthy." in text
    assert "PROBLEM" not in text
    assert "LIKE fallback" in text
