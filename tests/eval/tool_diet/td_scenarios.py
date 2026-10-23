"""The fixed prompt set of the tool-diet accuracy eval (MTD-accuracy-eval).

Each scenario is a plain request an agent gets, the ONE tool (by its flat name) that answers
it, and the values the call must carry. Prompts never name a tool or an action; a family
surface is right when it routes to the same member, a flat surface when it names it.
"""

from __future__ import annotations

from typing import NamedTuple


class Scenario(NamedTuple):
    prompt: str
    tool: str
    values: tuple[str, ...] = ()


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "Record that the critic gate passed for item T-1; evidence: all 40 tests green.",
        "ddflow_gate_record",
        ("T-1", "critic", "passed"),
    ),
    Scenario(
        "I found a bug: the parser crashes on empty input. File it against item T-1.",
        "ddflow_bug_found",
        ("T-1", "parser"),
    ),
    Scenario("Claim item T-2 so I can start working on it.", "ddflow_claim", ("T-2",)),
    Scenario("Where does item T-3 stand in its gate pipeline?", "ddflow_gate_status", ("T-3",)),
    Scenario(
        "Save a lesson called 'Format first': always run the formatter before committing.",
        "ddflow_lesson_add",
        ("Format first",),
    ),
    Scenario(
        "Bug B-9 is fixed; its regression test is tests/test_x.py::test_y.",
        "ddflow_bug_fixed",
        ("B-9", "tests/test_x.py::test_y"),
    ),
    Scenario(
        "Add a task T-7 titled 'Add retries' to phase P-1.",
        "ddflow_task_add",
        ("T-7", "Add retries"),
    ),
    Scenario(
        "Skip the docs gate on item T-1 because the change only touches tests.",
        "ddflow_gate_skip",
        ("T-1", "docs"),
    ),
    Scenario(
        "Write down the operator's exact words for this session: 'ship it on friday'.",
        "ddflow_session_prompt",
        ("ship it on friday",),
    ),
    Scenario("Give up my lease on item T-4 without finishing it.", "ddflow_release", ("T-4",)),
    Scenario(
        "Record a decision titled 'Use sqlite': we store the queue in sqlite.",
        "ddflow_decision_add",
        ("Use sqlite",),
    ),
    Scenario("Merge item T-1 into main.", "ddflow_merge", ("T-1",)),
    Scenario("Item T-1 is done and merged; complete it.", "ddflow_complete", ("T-1",)),
    Scenario("Show every rule that mentions the word 'naming'.", "ddflow_rule_search", ("naming",)),
    Scenario("Find lessons about flaky tests.", "ddflow_lesson_search", ("flaky",)),
    Scenario("What is the state of the whole queue right now?", "ddflow_status", ()),
)
