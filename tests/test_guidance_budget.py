"""B91e1d67c87: project rules reach a fresh session, a subagent and a claim.

With a few advisory rules and hundreds of decisions the brief cut every rule ("7 more cut"),
the launch brief carried none and the claim block spent its budget on warn-level decisions.
A short charter (id and title, one line each) is reserved first and never cut.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle
from ddflow.api._base import _load
from ddflow.core.budget import Budget
from ddflow.services.guidance import charter as CH
from ddflow.services.guidance import inject as GI
from ddflow.services.guidance.record import GuidanceRecord

RULES = 7
DECISIONS = 120


def _project(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == 0
    (repo / "CLAUDE.md").write_text("# rules\n")
    for i in range(RULES):
        args = ("rule", "add", "--id", f"r-charter{i}", "--title", f"Charter title {i}",
                "--content", f"rule {i} says: " + "keep things tidy. " * 30, "--new")  # fmt: skip
        assert run_cli(repo, *args)[0] == 0, args
    for i in range(DECISIONS):
        args = ("decision", "add", "--id", f"D-w{i:03d}", "--title", f"warn decision {i}",
                "--decision", "the zebra cache is rotated by size. " * 30,
                "--new")  # fmt: skip
        assert run_cli(repo, *args)[0] == 0, args
    assert run_cli(repo, "task", "add", "T1", "--title", "zebra", "--globs", "src/z.py")[0] == 0


def test_the_brief_lists_every_rule_charter_line(repo):
    _project(repo)
    data = lifecycle.brief(repo, item="T1", agent="a1").data
    for i in range(RULES):
        assert f"r-charter{i}" in data["text"] and f"Charter title {i}" in data["text"], i
    assert "more cut to fit session.brief_max_tokens: r-" not in data["text"]
    assert data["approx_tokens"] <= 1200 * 1.05


def test_a_pinned_rule_survives_the_cut():
    pinned = GuidanceRecord(id="r-pin", kind="rule", title="Pinned law", body="never",
                            enforcement="block")  # fmt: skip
    advice = [GuidanceRecord(id=f"r-a{i}", kind="rule", title=f"A {i}") for i in range(30)]
    got = CH.lines([*advice, pinned], tokens=60, title_chars=100)
    assert "r-pin" in got[0] and "pinned" in got[0]  # leads, so a cut from the bottom spares it
    assert "more rules: r-a" in got[-1]  # the cut names what it dropped


def test_the_launch_brief_carries_the_charter(repo):
    _project(repo)
    out = run_cli(repo, "brief", "--for-agent", "sub-1", "T1")[1]
    for i in range(RULES):
        assert f"r-charter{i}" in out, i


def test_the_charter_cap_is_a_knob(repo):
    _project(repo)
    assert run_cli(repo, "config", "session.rules_charter_tokens", "0")[0] == 0
    text = lifecycle.brief(repo, item="T1", agent="a1").data["text"]
    assert "Charter title 0" not in text


def test_the_claim_block_reserves_slots_for_rules(repo):
    _project(repo)
    _log, cfg, st = _load(repo, "a1")
    got = GI.for_item(cfg, st, "T1", budget=GI.door_budget(cfg))
    held = [r.id for r in got.records("rule")]
    assert len(held) == cfg.session.claim_rules_reserved == 2
    assert all(i.startswith("r-charter") for i in held), held
    none = GI.for_item(cfg, st, "T1", budget=Budget(1, "tokens"), reserve_rules=0)
    assert none.records("rule") == []


def test_the_charter_survives_the_backstop_on_a_tiny_budget(repo):
    _project(repo)
    assert run_cli(repo, "config", "session.brief_max_tokens", "420")[0] == 0
    data = lifecycle.brief(repo, item="T1", agent="a1").data
    for i in range(RULES):
        assert f"r-charter{i}" in data["text"], (i, data["text"])
    assert data["approx_tokens"] <= 420 * 1.1


def test_a_charter_that_cannot_fit_is_counted_not_dumped():
    rules = [GuidanceRecord(id=f"r-x{i}", kind="rule", title="T" * 90) for i in range(8)]
    got = CH.lines(rules, tokens=3, title_chars=100)
    assert len(got) == 1 and "8 more rules: r-x0, r-x1" in got[0] and "and 2 more" in got[0]


def test_a_pinned_rule_is_listed_even_when_the_charter_has_no_room():
    pinned = GuidanceRecord(id="r-pin", kind="rule", title="Pinned law", enforcement="block")
    plain = [GuidanceRecord(id=f"r-p{i}", kind="rule", title="x") for i in range(4)]
    got = CH.lines([*plain, pinned], tokens=1, title_chars=100)
    assert got[0].startswith("- [r-pin]") and "more rules: r-p0" in got[-1]


def test_the_cut_line_is_reserved_only_for_the_rules_after_the_one_listed():
    rules = [GuidanceRecord(id=f"r-{c}", kind="rule", title="t") for c in "ab"]
    exact = (
        CH.approx_tokens(CH.HEADING)
        + CH.approx_tokens(CH.LEAD)
        + CH.approx_tokens("- [r-a] t")
        + CH.approx_tokens(CH._more(rules[1:]))
    )
    got = CH.lines(rules, tokens=exact, title_chars=100)
    # reserving the cut line for BOTH rules would have dropped the first one
    assert got == ["- [r-a] t", "- [r-b] t"]


def test_a_rule_the_charter_cut_keeps_its_text_in_the_rules_block():
    from types import SimpleNamespace

    from ddflow.api.lifecycle.brief import _rules_block

    listed = GuidanceRecord(id="r-listed", kind="rule", title="L", body="listed body")
    cut = GuidanceRecord(id="r-cut", kind="rule", title="C", body="cut body")
    gov = SimpleNamespace(records=lambda kind: [listed, cut])
    got = _rules_block(gov, charter="- [r-listed] L\n- _1 more rules: r-cut; `ddflow rule list`_")
    assert "r-cut" in got and "r-listed" not in got


def test_listed_reads_the_ids_of_charter_lines_not_of_the_cut_line():
    text = "- [r-a] A\n- [r-b] B (pinned)\n- _2 more rules: r-c, r-d; `ddflow rule list`_"
    assert CH.listed(text) == {"r-a", "r-b"}
