"""A feature item under-declares its globs (B-item-globs-omit-wiring-files): it names the
file it creates but not the shared wiring files that register it, so
`enforce.commit_without_lease` refuses the first write. A task that declares `adds`
(`services.wiring`: knob|event|command) has those wiring globs added to its declared globs
in the one `add_task` choke point, so `task add` and the bug auto-filer both expand them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.core.model import State
from ddflow.infra.log import EventLog
from ddflow.services import items as IT
from ddflow.services import wiring as W

WHY = "a test"


def _log(repo: Path) -> EventLog:
    (repo / ".ddflow").mkdir(exist_ok=True)
    return EventLog(repo, "agent-test")


def _add(draft: IT.TaskDraft, repo: Path) -> IT.Added:
    log, st = _log(repo), State()
    return IT.add_task(log, st, Config.load(repo), draft, dedupe=WHY)


# -- the declarative map ---------------------------------------------------------------------


def test_the_wiring_map_names_exactly_the_three_feature_classes():
    assert W.FEATURE_CLASSES == ("knob", "event", "command")
    assert W.WIRING["knob"] == ("ddflow/config_sections/*.py", "ddflow/templates/default.toml")
    assert W.WIRING["event"] == ("ddflow/templates/events/*.toml",)
    assert set(W.WIRING) == set(W.FEATURE_CLASSES)


def test_expand_keeps_the_item_own_globs_first_and_adds_the_wiring_without_repeats():
    assert W.expand(["a/**", "ddflow/templates/default.toml"], ["knob"]) == [
        "a/**",
        "ddflow/templates/default.toml",
        "ddflow/config_sections/*.py",
    ]
    assert W.expand(None, ["event"]) == ["ddflow/templates/events/*.toml"]
    assert W.expand(["a/**"], None) == ["a/**"]
    assert W.expand(["a/**"], []) == ["a/**"]


def test_an_unknown_feature_class_is_named_not_guessed():
    assert W.problem(None) == ""
    assert W.problem(["knob"]) == ""
    assert "unknown feature class" in W.problem(["knob", "page"])


# -- add_task auto-expands the globs ---------------------------------------------------------


def test_a_task_declaring_a_new_knob_is_auto_given_the_knob_wiring_globs(repo):
    log, st = _log(repo), State()
    IT.add_task(log, st, Config.load(repo), IT.TaskDraft("T-knob", title="one", adds=["knob"]), dedupe=WHY)
    assert st.items["T-knob"].globs == [
        "ddflow/config_sections/*.py",
        "ddflow/templates/default.toml",
    ]


@pytest.mark.parametrize(
    ("adds", "wanted"),
    [
        ("event", ["ddflow/templates/events/*.toml"]),
        (
            "command",
            [
                "ddflow/surfaces/declared/*",
                "ddflow/surfaces/commands/*",
                "ddflow/surfaces/parsers/*",
                "ddflow/api/__init__.py",
                "ddflow/surfaces/tools/*",
            ],
        ),
    ],
)
def test_event_and_command_tasks_are_given_their_wiring_globs(repo, adds, wanted):
    log, st = _log(repo), State()
    IT.add_task(log, st, Config.load(repo), IT.TaskDraft("T", title="one", adds=[adds]), dedupe=WHY)
    assert st.items["T"].globs == wanted


def test_declared_globs_are_kept_beside_the_wiring_globs(repo):
    log, st = _log(repo), State()
    IT.add_task(
        log,
        st,
        Config.load(repo),
        IT.TaskDraft("T", title="one", globs=["ddflow/config_sections/lease.py"], adds=["knob"]),
        dedupe=WHY,
    )
    assert st.items["T"].globs == [
        "ddflow/config_sections/lease.py",
        "ddflow/config_sections/*.py",
        "ddflow/templates/default.toml",
    ]


def test_an_added_knob_keeps_the_event_carrying_the_adds_declaration(repo):
    log, st = _log(repo), State()
    IT.add_task(log, st, Config.load(repo), IT.TaskDraft("T", title="one", adds=["knob"]), dedupe=WHY)
    [ev] = [e.data for e in log.read_all() if e.subject == "T" and e.kind == "task.added"]
    assert ev["adds"] == ["knob"]
    assert ev["globs"] == ["ddflow/config_sections/*.py", "ddflow/templates/default.toml"]


def test_an_unknown_adds_is_refused_like_an_unknown_kind(repo):
    out = _add(IT.TaskDraft("T", title="one", adds=["page"]), repo)
    assert not out.ok and "unknown feature class" in out.problem

