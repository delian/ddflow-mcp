"""B158 + B159: re-stating the rules after a compaction, without becoming a banner.

`initialize` delivers the instruction block ONCE. After a context compaction the model may
retain none of it, and MCP has no server->client context-injection primitive — the three
that exist are `roots/list`, `sampling/createMessage` and `elicitation/create`, and none
injects anything. A footer on tool results is the only channel that survives, because an
agent driving ddflow calls tools continuously.

The design constraint is the whole difficulty. This repo already learned that **a standing
banner is one readers learn to skip, and then they skip the one that mattered**
(`test_the_handshake_stays_quiet_once_the_import_is_finished`). So the footer is cadenced,
config-gated, and — the part that actually matters — **stateful**: it names something that
happened and stops naming it once dealt with. The tests below are mostly about the silence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state as _state

from ddflow.config import Config
from ddflow.services import obligations as OB


def _eager(repo, **knobs):
    """Make the cadence fire on every call, so a test measures CONTENT not timing."""
    cfg = repo / ".ddflow" / "config.toml"
    body = "\n[reinstruct]\nevery_calls = 1\nevery_seconds = 0\n"
    for k, v in knobs.items():
        body += f"{k} = {v!r}\n".replace("'", '"') if isinstance(v, str) else f"{k} = {v}\n"
    cfg.write_text(cfg.read_text() + body)


def _call(repo, tool="ddflow_status", **args):
    from ddflow.surfaces.mcp import Server

    return Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
    )["result"]


def _footer_of(result) -> str:
    return next(
        (c["text"] for c in result["content"] if c["text"].startswith("ddflow: left undone")), ""
    )


# -- B159: what it finds, and that it is specific ------------------------------------------


def test_an_open_bug_is_named_with_the_call_that_closes_it(repo):
    """Specific and actionable, or it is a banner. The id, the count, the remedy."""
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "the fold drops seq")

    items = OB.outstanding(_state(repo), Config.load(repo))
    assert [o.kind for o in items] == ["open_bug"], items
    assert "B1" in items[0].detail
    assert "ddflow_bug_fixed" in items[0].remedy


def test_a_skipped_gate_is_reported_as_skipped_not_as_passed(repo):
    """A skip is a recorded DECISION and legitimate. A skip nobody revisited is a step the
    pipeline claims to enforce and did not."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    pipeline = json.loads(run_cli(repo, "--json", "workflow")[1])["task_pipeline"]
    run_cli(repo, "gate", "skip", "T1", pipeline[0], "--reason", "not applicable here")

    kinds = [o.kind for o in OB.outstanding(_state(repo), Config.load(repo))]
    assert "skipped_gate" in kinds, kinds


def test_finishing_work_with_no_lesson_is_reported_only_after_several(repo):
    """One task finishing without a lesson is normal, and reporting it would make this
    noise. The knob is what separates the two, and it is a knob on purpose."""
    run_cli(repo, "init")
    cfg = Config.load(repo)
    threshold = cfg.lessons.reflect_after_items
    assert threshold >= 2, "a threshold of 1 would report the first task ever completed"

    for i in range(threshold):
        run_cli(repo, "task", "add", f"T{i}", "--globs", f"f{i}.py")
        run_cli(repo, "claim", f"T{i}")
        run_cli(repo, "complete", f"T{i}", "--force")
        if i < threshold - 1:
            kinds = [o.kind for o in OB.outstanding(_state(repo), cfg)]
            assert "no_lessons" not in kinds, f"reported after only {i + 1} item(s)"

    kinds = [o.kind for o in OB.outstanding(_state(repo), cfg)]
    assert "no_lessons" in kinds, kinds


# -- the silence, which is the hard part ---------------------------------------------------


def test_a_clean_project_gets_NO_footer_at_all(repo):
    """The property that stops this being a banner. Nothing outstanding, nothing said —
    not a cheerful "all clear", which is the same thing readers learn to skip."""
    run_cli(repo, "init")
    _eager(repo)
    assert OB.footer(_state(repo), Config.load(repo)) == ""
    assert _footer_of(_call(repo)) == ""


def test_the_footer_STOPS_once_the_obligation_is_discharged(repo):
    """Cannot be trained out by repetition, because repetition means it was ignored.

    This is the difference between this and a standing banner, stated as a test: the same
    project, the same tool, and the footer is gone because the thing it named is done.
    """
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "something broke")
    _eager(repo)

    assert "B1" in _footer_of(_call(repo)), "the open bug was never reported"
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_x.py").write_text("def test_y():\n    pass\n")
    run_cli(repo, "bug", "fixed", "B1", "--regression-test", "tests/test_x.py::test_y")
    assert _footer_of(_call(repo)) == "", "it kept reporting a bug that is closed"


def test_the_cadence_holds_its_tongue_between_footers(repo):
    """A footer on every call costs tokens on every call and is skipped by the third."""
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + "\n[reinstruct]\nevery_calls = 3\nevery_seconds = 0\n")

    from ddflow.surfaces.mcp import Server

    srv = Server(repo)

    def once():
        r = srv.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_status", "arguments": {}},
            }
        )["result"]
        return bool(_footer_of(r))

    spoke = [once() for _ in range(6)]
    assert spoke == [False, False, True, False, False, True], spoke


def test_disabling_it_silences_it_completely(repo):
    """An operator who does not want this must be able to turn it off, not turn it down."""
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text() + "\n[reinstruct]\nenabled = false\nevery_calls = 1\nevery_seconds = 0\n"
    )
    assert _footer_of(_call(repo)) == ""


def test_the_footer_never_breaks_the_answer_it_rides_on(repo):
    """A courtesy on top of a result the caller asked for. `content[0]` is still the body,
    so a machine consumer doing `json.loads(content[0].text)` is untouched — which is the
    regression three demo scenarios already paid for once."""
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    _eager(repo)

    result = _call(repo)
    assert _footer_of(result), "the footer did not appear, so this proves nothing"
    body = json.loads(result["content"][0]["text"])
    assert "tasks" in body, body


def test_a_broken_obligation_check_cannot_fail_the_tool_call(repo, monkeypatch):
    """The footer is a courtesy. A courtesy that turns a working tool into a failure is
    worse than no courtesy at all."""
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    _eager(repo)

    def boom(*_a, **_k):
        raise RuntimeError("the obligation check is broken")

    monkeypatch.setattr(OB, "footer", boom)
    result = _call(repo)
    assert result["isError"] is False, result
    assert json.loads(result["content"][0]["text"])["tasks"] is not None
    assert _footer_of(result) == ""


def test_the_knobs_are_documented_and_reachable(repo):
    """A knob with no documentation is one nobody knows to set; `config --explain` is where
    an operator looks."""
    run_cli(repo, "init")
    _code, out, _err = run_cli(repo, "config", "--explain", "--filter", "reinstruct")
    for knob in ("enabled", "every_calls", "every_seconds", "max_items"):
        assert f"reinstruct.{knob}" in out, out
    assert "compaction" in out, "the reason the feature exists is not in its documentation"
