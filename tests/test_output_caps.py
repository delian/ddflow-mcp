"""Default output caps of board, doctor, recover, next and status (UX-output-caps).

A big queue used to print megabytes from `board` and overrun an MCP client's 25k-token
tool-result cap. Each of these commands now prints a bounded default, ends in a footer that
names the exact next call, and takes `--full` / `full=true` to lift the caps. The limits are
the `[output]` knobs, settable in the environment as well as the config files."""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import outcome as O
from ddflow.infra.log import EventLog
from ddflow.views import output_caps as OC

PHASES = 30
PER_PHASE = 30  # 900 tasks: a board of well over a hundred KB


def _big_queue(repo) -> None:
    run_cli(repo, "init")
    log = EventLog(repo, "agent-test")
    for p in range(PHASES):
        log.append("phase.added", f"P{p:02d}", {"title": f"phase number {p} " + "y" * 60})
        for t in range(PER_PHASE):
            log.append(
                "task.added",
                f"P{p:02d}-T{t:02d}",
                {
                    "title": f"task {p}.{t} " + "x" * 90,
                    "parent": f"P{p:02d}",
                    "needs": [f"P{p:02d}-T{t - 1:02d}"] if t else [],
                    "globs": [f"d{p:02d}/f{t:02d}.py"],
                },
            )


def _mcp(repo, tool: str, **arguments):
    from ddflow.surfaces.mcp import Server

    r = Server(repo, agent="agent-test").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )["result"]
    return r["content"][0]["text"]


def _cfg(**output) -> Config:
    cfg = Config()
    for k, v in output.items():
        setattr(cfg.output, k, v)
    return cfg


def _out(n: int) -> O.Outcome:
    return O.ok("next", ready=[], blocked=[{"item": f"I{i}", "reason": "deps"} for i in range(n)])


def test_a_list_over_the_cap_is_cut_and_says_how_to_get_the_rest():
    out = OC.apply(None, _out(120), "next", cfg=_cfg(max_rows=50))
    assert len(out.data["blocked"]) == 50
    assert out.data["truncated"]["lists"]["blocked"] == 120
    nxt = out.data["next"]
    assert nxt["cursor_cli"] == "ddflow next --cursor 50"
    assert nxt["cursor_mcp"] == "ddflow_next cursor=50"
    assert nxt["full_cli"] == "ddflow next --full" and nxt["full_mcp"] == "ddflow_next full=true"
    assert "ddflow next --full" in out.data["_footer"]


def test_a_cursor_pages_the_list():
    out = OC.apply(None, _out(120), "next", cursor=50, cfg=_cfg(max_rows=50))
    assert [b["item"] for b in out.data["blocked"]][:1] == ["I50"]
    assert out.data["next"]["cursor_cli"] == "ddflow next --cursor 100"
    last = OC.apply(None, _out(120), "next", cursor=100, cfg=_cfg(max_rows=50))
    assert len(last.data["blocked"]) == 20 and "cursor_cli" not in last.data["next"]


def test_full_lifts_the_cap_and_a_small_answer_is_untouched():
    assert "truncated" not in OC.apply(None, _out(120), "next", full=True, cfg=_cfg()).data
    assert "truncated" not in OC.apply(None, _out(10), "next", cfg=_cfg()).data


def test_an_org_lock_keeps_the_cap_and_says_the_lift_is_off():
    out = OC.apply(None, _out(120), "next", full=True, cfg=_cfg(allow_full=False))
    assert len(out.data["blocked"]) == 50
    assert "allow_full" in out.data["_footer"] and "full_cli" not in out.data["next"]


def test_text_is_cut_on_a_line_boundary_under_the_char_cap():
    text = "\n".join(f"line {i} " + "z" * 80 for i in range(2000))
    out = OC.apply(None, O.ok("board", text=text, phases=[]), "board", cfg=_cfg(max_chars=5000))
    assert len(out.data["text"]) < 5000 + 400
    assert out.data["text"].split("\n\n")[0].endswith("z" * 80), "cut at a line end"
    assert out.data["truncated"]["chars_cut"] > 0 and "ddflow board --full" in out.data["text"]


def test_rows_halve_until_the_json_body_fits():
    rows = [{"item": f"I{i}", "reason": "r" * 400} for i in range(50)]
    out = OC.apply(None, O.ok("next", blocked=rows), "next", cfg=_cfg(max_chars=6000))
    assert len(json.dumps(out.data["blocked"])) <= 6000
    assert out.data["truncated"]["lists"]["blocked"] == 50


def test_the_knobs_exist_with_docs_and_the_environment_sets_them(monkeypatch):
    monkeypatch.setenv("DDFLOW_OUTPUT_MAX_ROWS", "7")
    assert Config.load(None).output.max_rows == 7
    out = Config().output
    assert (out.max_rows, out.max_chars, out.allow_full) == (50, 30000, True)


def test_the_board_stays_under_the_ceiling_on_a_large_log_and_full_prints_all(repo):
    _big_queue(repo)
    code, full, err = run_cli(repo, "board", "--full")
    assert code == 0, err
    assert len(full) > 100_000 and "P29-T29" in full
    code, text, err = run_cli(repo, "board")
    assert code == 0, err
    assert len(text) < 30_000 + 600, len(text)
    assert "ddflow board --full" in text and "full=true" in text
    code, js, err = run_cli(repo, "--json", "board")
    assert code == 0, err
    body = json.loads(js)
    assert len(js) < 30_000 + 4000, len(js)
    assert body["truncated"]["lists"]["task_rows"] >= PHASES * PER_PHASE
    assert body["next"]["full_cli"] == "ddflow board --full"
    code, js, err = run_cli(repo, "--json", "board", "--full")
    assert sum(len(p["tasks"]) for p in json.loads(js)["phases"]) >= PHASES * PER_PHASE


def test_board_is_bounded_and_liftable_over_mcp_too(repo):
    _big_queue(repo)
    first = _mcp(repo, "ddflow_board")
    assert len(first) < 30_000 + 600 and "ddflow_board full=true" in first
    assert "ddflow_board phase=ID" in first, "names how to narrow"
    assert len(_mcp(repo, "ddflow_board", full=True)) > 100_000


def test_next_status_and_doctor_stay_bounded_on_the_large_log(repo):
    _big_queue(repo)
    for argv in (("--json", "next"), ("--json", "status"), ("next",), ("status",), ("doctor",)):
        code, out, err = run_cli(repo, *argv)
        assert code in (0, 1, 2), (argv, err)
        assert len(out) < 2 * 30_000 + 5000, (argv, len(out))
    for tool in ("ddflow_next", "ddflow_status", "ddflow_doctor", "ddflow_recover"):
        assert len(_mcp(repo, tool)) < 2 * 30_000 + 5000, tool
    code, out, err = run_cli(repo, "--json", "next", "--full")
    assert len(json.loads(out)["blocked"]) > 50 or code == 2


@pytest.mark.parametrize("command", sorted(OC.COMMANDS))
def test_every_capped_command_has_a_full_flag(repo, command):
    _code, out, err = run_cli(repo, command, "--help")
    assert "--full" in out + err, command
