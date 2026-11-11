"""Default output caps of board, doctor, recover, next and status (UX-output-caps).

A big queue used to print megabytes from `board` and overrun an MCP client's 25k-token
tool-result cap. Each of these commands now prints a bounded default, ends in a footer that
names the exact next call, and takes `--full` / `full=true` to lift the caps. The limits are
the `[output]` knobs, settable in the environment as well as the config files."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import outcome as O
from ddflow.infra.log import EventLog
from ddflow.surfaces.parsers.reporting import render_recover
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


def test_a_refused_lift_does_not_advertise_the_lift_and_zero_knobs_do_not_undo_the_lock():
    out = OC.apply(None, _out(120), "next", full=True, cfg=_cfg(allow_full=False, max_rows=0))
    assert len(out.data["blocked"]) == 50, "max_rows = 0 does not lift a locked cap"
    assert "all" not in out.data["truncated"]


def test_a_cut_that_only_hit_the_render_lists_is_still_reported():
    out = O.ok("recover", found=[], _render={"found": list(range(100))})
    out = OC.apply(None, out, "recover", cfg=_cfg(max_rows=15))
    assert len(out.data["_render"]["found"]) == 15
    assert "truncated" in out.data and out.data["_footer"]


def test_recover_is_capped_by_default_and_full_is_off_by_default(repo):
    _code, out, _err = run_cli(repo, "recover", "--help")
    assert "--full" in out and "--cursor" in out
    from ddflow.surfaces.declared.capflags import FULL

    assert FULL.default is False


def test_one_oversized_row_is_clipped_so_the_cap_holds():
    out = O.ok("doctor", findings=[{"detail": "x" * 100_000}])
    out = OC.apply(None, out, "doctor", cfg=_cfg(max_chars=1000, max_rows=10))
    assert len(json.dumps(out.data["findings"])) < 1500
    assert out.data["truncated"]["clipped_to"] <= 500 and "long values cut" in out.data["_footer"]


def test_a_row_of_many_fields_still_fits_and_a_default_locked_run_hides_the_lift():
    row = {f"f{i}": "a" * 100 for i in range(200)}
    out = OC.apply(None, O.ok("next", blocked=[row]), "next", cfg=_cfg(max_chars=3000), wire=True)
    assert len(json.dumps(out.data["blocked"])) < 1500
    locked = OC.apply(None, _out(120), "next", cfg=_cfg(allow_full=False))
    assert "all" not in locked.data["truncated"] and "full_cli" not in locked.data["next"]
    stale = O.ok("next", blocked=_out(120).data["blocked"], truncated={"all": "old"})
    assert "all" not in OC.apply(None, stale, "next", cfg=_cfg(allow_full=False)).data["truncated"]


def test_the_last_resort_marks_only_the_offending_list():
    wide = {f"f{i}": "a" * 100 for i in range(200)}
    out = O.ok("next", ready=[{"id": "step-1"}], blocked=[wide])
    out = OC.apply(None, out, "next", cfg=_cfg(max_chars=3000), wire=True)
    assert out.data["ready"] == [{"id": "step-1"}]
    assert "too large" in out.data["blocked"][0]["cut"]


def test_an_empty_key_does_not_break_the_last_resort():
    out = O.ok("next", **{"": [{"id": "x" * 500}]})
    assert OC.apply(None, out, "next", cfg=_cfg(max_chars=100), wire=True).data["truncated"]


def test_next_keeps_the_blocked_by_reason_count_when_the_cap_cuts_and_recover_leaves_a_note():
    out = OC.apply(None, _out(120), "next", cfg=_cfg(max_rows=50))
    assert out.data["truncated"]["blocked_by_reason"] == {"deps": 120}
    from ddflow.surfaces.mcp_bound import bound_recover

    big = O.ok("recover", found=[{"item": f"I{i}"} for i in range(120)])
    big = OC.apply(None, big, "recover", cfg=_cfg(max_rows=50))
    body, note = bound_recover(big.body("found"), {})
    assert len(body) == 50 and "ddflow_recover full=true" in note
    huge = O.ok("next", blob="y" * 50_000)
    assert (
        len(OC.apply(None, huge, "next", cfg=_cfg(max_chars=1000), wire=True).data["blob"]) < 1000
    )


def test_the_config_command_writes_the_knob_and_the_answer_follows_it(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "config", "output.max_rows", "40")
    assert code == 0, err
    assert Config.load(repo).output.max_rows == 40


def _plan(n: int) -> O.Outcome:
    acts = [
        SimpleNamespace(cls="propose", item=f"I{i}", attempts=0, reason="r", command="c")
        for i in range(n)
    ]
    rows = [{"item": a.item} for a in acts]
    render = {"plan": True, "actions": acts, "recoveries": []}
    return O.ok("recover", found=rows, count=n, salvageable=0, paused=False, _render=render)


def test_the_recovery_plan_prose_keeps_its_real_count_says_it_was_cut_and_pages():
    out = OC.apply(None, _plan(120), "recover", cfg=_cfg(max_rows=50))
    text = render_recover(out, None)
    assert "recovery plan: 120 action(s)" in text, "the count is the plan's, not the page's"
    assert "ddflow recover --cursor 50" in text and "I49 " in text and "I50 " not in text
    page = render_recover(OC.apply(None, _plan(120), "recover", cursor=50, cfg=_cfg()), None)
    assert "I50 " in page and "I49 " not in page


def test_the_plain_recover_prose_keeps_its_real_count():
    rows = [
        SimpleNamespace(
            item=f"I{i}", kind="k", holder="h", worktree="", advice="a", salvageable=False
        )
        for i in range(120)
    ]
    out = O.ok(
        "recover",
        found=[{"item": r.item} for r in rows],
        count=120,
        salvageable=0,
        _render={"found": rows, "salvageable": []},
    )
    text = render_recover(OC.apply(None, out, "recover", cfg=_cfg(max_rows=50)), None)
    assert text.startswith("120 recoverable situation(s)") and "ddflow recover --full" in text


def test_the_doctor_cli_is_cut_in_text_and_json_and_full_lifts_it(repo, monkeypatch):
    """A cap this small must bite whatever the fixture says: a size assertion on a short
    report passed while `cmd_doctor` never called the cap at all."""
    run_cli(repo, "init")
    _code, whole, _err = run_cli(repo, "doctor", "--full")
    assert len(whole) > 200 and "ddflow doctor --full" not in whole
    monkeypatch.setenv("DDFLOW_OUTPUT_MAX_CHARS", "200")
    _code, text, _err = run_cli(repo, "doctor")
    assert "ddflow doctor --full" in text and len(text) < len(whole) + 400
    assert text.split("\n\n[")[0] != whole.rstrip(), "the report itself was cut"
    _code, body, _err = run_cli(repo, "--json", "doctor")
    assert json.loads(body)["next"]["full_cli"] == "ddflow doctor --full"
    _code, again, _err = run_cli(repo, "doctor", "--full")
    assert "ddflow doctor --full" not in again


@pytest.mark.parametrize("command", ["board", "next", "status"])
def test_every_surface_of_a_command_bites_at_a_tiny_cap(repo, monkeypatch, command):
    run_cli(repo, "init")
    log = EventLog(repo, "agent-test")
    for i in range(12):
        title = f"task {i} " + "x" * 90
        log.append("task.added", f"T{i:02d}", {"title": title, "globs": ["same.py"]})
    monkeypatch.setenv("DDFLOW_OUTPUT_MAX_ROWS", "2")
    monkeypatch.setenv("DDFLOW_OUTPUT_MAX_CHARS", "400")
    call = f"ddflow {command} --full"
    _code, text, _err = run_cli(repo, command)
    assert call in text, "the terminal answer"
    _code, body, _err = run_cli(repo, "--json", command)
    assert json.loads(body)["next"]["full_cli"] == call, "the --json answer"
    assert f"ddflow_{command} full=true" in _mcp(repo, f"ddflow_{command}"), "the MCP answer"


def test_non_ascii_text_costs_more_so_the_token_cap_holds():
    """Measured with the Qwen and DeepSeek tokenizers: 30000 characters are 5737 tokens of
    English, 52366 of rare CJK and 84470 of emoji, so a plain character count overran the
    25000-token cap on a non-ASCII report (rubber_duck finding #1)."""
    for char in ("\u9f98", "\U0001f600"):
        text = "\n".join(char * 50 for _ in range(1000))
        out = OC.apply(None, O.ok("doctor", text=text), "doctor", cfg=_cfg(max_chars=30000))
        kept = out.data["text"].split("\n\n[")[0]
        cost = sum(1 if c.isascii() else 4 for c in kept)
        assert 29000 < cost <= 30000, cost
    ascii_text = "\n".join("a" * 50 for _ in range(1000))
    out = OC.apply(None, O.ok("doctor", text=ascii_text), "doctor", cfg=_cfg(max_chars=30000))
    assert 29000 < len(out.data["text"].split("\n\n[")[0]) <= 30000


def test_a_weight_of_one_counts_plain_characters():
    text = "\n".join("\u9f98" * 50 for _ in range(1000))
    cfg = _cfg(max_chars=3000, non_ascii_weight=1)
    kept = OC.apply(None, O.ok("doctor", text=text), "doctor", cfg=cfg).data["text"]
    assert 2900 < len(kept.split("\n\n[")[0]) <= 3000


def test_the_smallest_cap_on_a_json_body_is_still_a_cap():
    out = O.ok("board", text="line one\nline two\n" * 50, phases=[])
    out = OC.apply(None, out, "board", cfg=_cfg(max_chars=1), wire=True)
    assert out.data["truncated"]["chars_cut"] > 0


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
    assert out.non_ascii_weight == 4


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


def test_status_by_tag_json_keeps_the_budget_of_plain_status(repo, monkeypatch):
    """`--by-tag` goes through its own handler: a `--json` body gets the same budget there
    as in `cmd_status` (the whole `max_chars`: status has no prose half), or the two modes
    of one command disagree."""
    run_cli(repo, "init")
    log = EventLog(repo, "agent-test")
    for i in range(40):
        log.append(
            "task.added",
            f"T{i:02d}",
            {"title": f"task {i} " + "x" * 90, "globs": ["same.py"], "tags": [f"k:v{i}"]},
        )
    _code, whole, _err = run_cli(repo, "--json", "status", "--by-tag", "k", "--full")
    chars = int(len(whole) * 0.75)
    monkeypatch.setenv("DDFLOW_OUTPUT_MAX_CHARS", str(chars))
    _code, text, _err = run_cli(repo, "--json", "status", "--by-tag", "k")
    body = json.loads(text)
    assert "truncated" in body, "three quarters of the body is over the budget"
    for extra in ("truncated", "next"):
        body.pop(extra)
    size = len(json.dumps(body, indent=2))
    assert chars // 2 < size <= chars, "held to the whole budget, not its half"


def _heavy_row(item: str) -> dict:
    """A ready item as `next` really sends it: flat fields beside recorded gate evidence."""
    return {
        "id": item,
        "title": "t",
        "gates": {"ci": {"ids": [f"tests/t{i}" for i in range(4000)]}},
    }


def test_a_heavy_field_of_a_row_goes_before_the_row_itself():
    """`next` on a live queue answered `ready: [{cut: ...}]`: the one thing it exists to say
    was replaced because each ready item carries tens of KB of gate evidence."""
    out = O.ok("next", ready=[_heavy_row("A"), _heavy_row("B")], blocked=[])
    out = OC.apply(None, out, "next", cfg=_cfg())
    assert [r["id"] for r in out.data["ready"]] == ["A", "B"]
    assert isinstance(out.data["ready"][0]["gates"], dict), "the field keeps its type"
    assert out.data["truncated"]["fields_cut"] == 2
    assert "2 nested field(s) cut" in out.data["_footer"]
    assert OC._size(out.data) < 30000


def test_a_list_the_last_resort_replaces_keeps_its_real_length():
    wide = {f"f{i}": "a" * 100 for i in range(200)}
    out = O.ok("next", ready=[], blocked=[wide] * 120)
    out = OC.apply(None, out, "next", cfg=_cfg(max_chars=3000), wire=True)
    assert out.data["truncated"]["lists"]["blocked"] == 120


def test_a_page_past_the_end_still_says_so_in_prose():
    """A cursor past the last row printed nothing at all: no count and no next call."""
    plan = render_recover(OC.apply(None, _plan(120), "recover", cursor=999, cfg=_cfg()), None)
    assert "recovery plan: 120 action(s)" in plan and "ddflow recover --full" in plan
    rows = [
        SimpleNamespace(
            item=f"I{i}", kind="k", holder="h", worktree="", advice="a", salvageable=False
        )
        for i in range(3)
    ]
    out = O.ok(
        "recover",
        found=[{"item": r.item} for r in rows],
        count=3,
        salvageable=0,
        _render={"found": rows, "salvageable": []},
    )
    text = render_recover(OC.apply(None, out, "recover", cursor=999, cfg=_cfg()), None)
    assert text.startswith("3 recoverable situation(s)") and "ddflow recover --full" in text


def test_next_lane_answer_goes_through_the_cap_like_the_queue(repo, monkeypatch):
    """`next --lane` takes the same --full/--cursor flags, so its answer is capped too."""
    from ddflow.surfaces.declared.lifecycle import navigate

    note = "reason " * 3000
    fake = SimpleNamespace(
        DEFAULT_NEXT_KIND="task",
        next_lane=lambda *a, **k: O.ok("next", lane="L", item="I1", note=note),
    )
    monkeypatch.setattr(navigate, "_api", lambda: fake)
    monkeypatch.setenv("DDFLOW_OUTPUT_MAX_CHARS", "1000")
    out = navigate._next(repo, {"lane": "L"}, "agent")
    assert len(json.dumps(out.data, default=str)) < len(note) and "truncated" in out.data
    # The lift it names is the lane call, not the queue's `next`.
    assert out.data["next"]["full_cli"] == "ddflow next --lane L --full"
    assert out.data["next"]["full_mcp"] == "ddflow_next lane=L full=true"
    full = navigate._next(repo, {"lane": "L", "full": True}, "agent")
    assert full.data["note"] == note


def _lane_footer(monkeypatch, repo, a):
    from ddflow.surfaces.declared.lifecycle import navigate

    fake = SimpleNamespace(
        DEFAULT_NEXT_KIND="task",
        next_lane=lambda *x, **k: O.ok("next", lane=a["lane"], item="I1", note="reason " * 3000),
    )
    monkeypatch.setattr(navigate, "_api", lambda: fake)
    monkeypatch.setenv("DDFLOW_OUTPUT_MAX_CHARS", "1000")
    return navigate._next(repo, a, "agent").data["next"]


def test_next_lane_footer_keeps_kind_and_phase(repo, monkeypatch):
    """The rerun a capped `next --lane L --kind K --phase P` names is the same selection."""
    nxt = _lane_footer(monkeypatch, repo, {"lane": "L", "kind": "doc", "phase": "P1"})
    assert nxt["full_cli"] == "ddflow next --lane L --kind doc --phase P1 --full"
    assert nxt["full_mcp"] == "ddflow_next lane=L kind=doc phase=P1 full=true"
    only = _lane_footer(monkeypatch, repo, {"lane": "L", "phase": "P1", "phase_only": True})
    assert only["full_cli"] == "ddflow next --lane L --phase P1 --phase-only --full"
    assert only["full_mcp"] == "ddflow_next lane=L phase=P1 phase_only=true full=true"
    default = _lane_footer(monkeypatch, repo, {"lane": "L"})
    assert default["full_cli"] == "ddflow next --lane L --full"


def test_next_lane_footer_quotes_a_lane_with_a_space(repo, monkeypatch):
    nxt = _lane_footer(monkeypatch, repo, {"lane": "wave 2"})
    assert nxt["full_cli"] == "ddflow next --lane 'wave 2' --full"
    assert nxt["full_mcp"] == "ddflow_next lane='wave 2' full=true"


def test_max_chars_doc_says_four_chars_a_token_is_ascii_only(repo):
    run_cli(repo, "init")
    text = run_cli(repo, "config", "--explain")[1]
    block = text[text.index("output.max_chars") :]
    block = (
        block[: block.index("output.non_ascii_weight")]
        if "output.non_ascii_weight" in block
        else block
    )
    assert "ASCII" in block and "non_ascii_weight" in block


def test_status_json_is_held_to_the_same_budget_on_cli_and_mcp():
    """Status carries no prose, so `--json` (wire) does not halve the budget: the CLI and the
    MCP call return the same rows. A body WITH prose (`board`) still halves."""

    def rows():
        return [{"item": f"I{i}", "title": "t" * 60} for i in range(40)]

    cfg = _cfg(max_chars=len(json.dumps(rows(), indent=2)) + 400, max_rows=50)
    plain = OC.apply(None, O.ok("status", in_flight=rows()), "status", cfg=cfg)
    wire = OC.apply(None, O.ok("status", in_flight=rows()), "status", cfg=cfg, wire=True)
    assert "truncated" not in wire.data and wire.data["in_flight"] == plain.data["in_flight"]
    prose = O.ok("next", text="p" * 30, blocked=rows())
    assert "truncated" in OC.apply(None, prose, "next", cfg=cfg, wire=True).data
