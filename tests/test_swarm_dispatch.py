"""SW-pull: the pure pull answer over score.pull and score.choose."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from conftest import run_cli

from ddflow import api
from ddflow.api.lifecycle import pull as PL
from ddflow.config import Config
from ddflow.core.model import DONE, RUNNING, Lease, State, fold
from ddflow.core.model import Item as QItem
from ddflow.core.schedule import caps
from ddflow.infra.log import EventLog
from ddflow.services import modelreg
from ddflow.services.lanes import launch_template as T
from ddflow.services.lanes.launch_template import Template
from ddflow.services.swarm import dispatch as D
from ddflow.services.swarm import ledger as L
from ddflow.services.swarm.score import Decision, Item, Lane, Live, World
from ddflow.services.swarm.score_config import Weights
from ddflow.surfaces.declared.lifecycle import navigate as NAV
from ddflow.surfaces.mcp import Server

W = Weights()
B = D.Bounds(note_chars=400, why_items=3)
FREE = D.Slots(in_flight=0, limit=4)
TPL = Template(name="ds", harness="kilo", route="lan", family="deepseek", model="deepseek-v4")
FACTS = D.LaneFacts(model="deepseek-v4", vendor="deepseek")


def _src(**kw):
    base = {"template": TPL, "facts": FACTS, "trust_declared": True, "approved": True}
    return D.LaneSource(**{**base, **kw})


ONE = (Item("a"),)


def _pull(items=ONE, src=None, world=None, slots=FREE, **kw):
    return D.pull_for("lane-1", src or _src(), items, world or World(), W, slots, B, **kw)


def test_offers_the_best_item_for_the_lane_with_its_note():
    got = _pull((Item("low"), Item("high", critical=1.0)))
    assert (got.status, got.lane, got.item) == (D.OFFER, "lane-1", "high")
    assert got.note.startswith("dispatch item=high lane=lane-1 family=deepseek model=deepseek-v4")
    assert "critical=3.000" in got.note


def test_bounds_read_the_swarm_knobs():
    cfg = Config()
    cfg.swarm.dispatch_note_chars = 77
    cfg.swarm.dispatch_why_items = 2
    assert D.Bounds.from_config(cfg) == D.Bounds(note_chars=77, why_items=2)


def test_the_same_inputs_give_the_same_answer():
    args = ((Item("b"), Item("a")), _src())
    assert _pull(*args) == _pull(*args)
    assert _pull(*args).item == "a"  # a tie goes to the lower id


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"template": None}, "no template"),
        ({"trust_declared": False}, "trust"),
        ({"approved": False}, "not approved"),
        ({"facts": replace(FACTS, vendor="")}, "family"),
        ({"facts": replace(FACTS, model="")}, "model"),
    ],
)
def test_an_unknown_lane_value_is_unavailable_and_offers_nothing(change, needle):
    got = _pull(src=_src(**change))
    assert got.status == D.UNAVAILABLE
    assert (got.item, got.note) == ("", "")
    assert any(needle in w for w in got.why), got.why


def test_a_template_family_the_catalog_contradicts_is_unavailable():
    got = _pull(src=_src(facts=replace(FACTS, vendor="anthropic", family="claude")))
    assert got.status == D.UNAVAILABLE
    assert any("contradicts" in w for w in got.why)


def test_the_lane_family_is_the_vendor_the_ledger_and_independence_key_on():
    t = replace(TPL, family="qwen", model="Qwen3-Coder")
    facts = D.LaneFacts(model="Qwen3-Coder", vendor="alibaba", family="qwen")
    assert D.lane_of("x", _src(template=t, facts=facts)).family == "alibaba"
    # ...so an author keyed by vendor is not independent of a lane templated by family id
    src = _src(template=t, facts=facts)
    got = _pull((Item("a", author_family="alibaba"),), src=src, role="reviewer")
    assert got.status == D.NONE


def test_the_fit_reads_the_ledger_cell_of_the_vendor():
    cell = L.Cell(items=20, first_pass=20)
    t = replace(TPL, family="qwen", model="Qwen3-Coder")
    facts = D.LaneFacts(model="Qwen3-Coder", vendor="alibaba", family="qwen")
    world = World(ledger=L.Ledger({L.Key("alibaba", "Qwen3-Coder", "task"): cell}))
    got = _pull(src=_src(template=t, facts=facts), world=world)
    assert got.decision.fit > 0.9


@pytest.mark.parametrize(
    ("slots", "needle"),
    [
        (D.Slots(in_flight=4, limit=4), "parallel limit"),
        (D.Slots(in_flight=9, limit=4), "parallel limit"),
        (D.Slots(in_flight=0, limit=4, paused="load is critical"), "load is critical"),
    ],
)
def test_the_parallel_limit_holds_the_offer(slots, needle):
    got = _pull(slots=slots)
    assert got.status == D.HOLD
    assert (got.item, got.note) == ("", "")
    assert any(needle in w for w in got.why), got.why


def test_one_free_slot_still_offers():
    assert _pull(slots=D.Slots(in_flight=3, limit=4)).status == D.OFFER


def test_an_unknown_lane_outranks_the_full_limit():
    got = _pull(src=_src(template=None), slots=D.Slots(in_flight=4, limit=4))
    assert got.status == D.UNAVAILABLE


def test_nothing_eligible_says_why_within_the_bound():
    live = (Live("other", ("src/a.py",)),)
    items = tuple(Item(f"i{n}", globs=("src/a.py",)) for n in range(9))
    got = _pull(items, world=World(live=live))
    assert got.status == D.NONE
    assert got.item == ""
    assert len(got.why) == B.why_items + 1
    assert got.why[0].startswith("i0: globs overlap the live lease on other")
    assert got.why[-1] == "and 6 more"


def test_no_items_is_a_plain_none():
    got = _pull(())
    assert (got.status, got.why) == (D.NONE, ("no item to offer",))


def test_a_reviewer_pull_passes_role_and_author():
    item = Item("a", author_family="deepseek")
    assert _pull((item,), role="reviewer").status == D.NONE
    other = Item("a", author_family="anthropic")
    got = _pull((other,), role="reviewer")
    assert got.status == D.OFFER
    assert " role=reviewer " in got.note


def test_place_uses_choose_to_pick_the_lane():
    good = Lane("good", "deepseek", "m")
    weak = Lane("weak", "anthropic", "n")
    world = World(
        ledger=L.Ledger({L.Key("deepseek", "m", "task"): L.Cell(items=20, first_pass=20)})
    )
    got = D.place(Item("a"), (weak, good), world, W, FREE, B)
    assert (got.status, got.lane, got.item) == (D.OFFER, "good", "a")
    assert got.note.startswith("dispatch item=a lane=good ")


def test_place_records_the_draw_of_an_exploring_pick():
    lanes = (Lane("seen", "deepseek", "m"), Lane("new", "anthropic", "n"))
    world = World(
        ledger=L.Ledger({L.Key("deepseek", "m", "task"): L.Cell(items=20, first_pass=20)})
    )
    got = D.place(Item("a"), lanes, world, W, FREE, B, draw=0.01)
    assert got.lane == "new"
    assert "draw=0.010" in got.note
    assert "explore: under-measured" in got.note


def test_place_holds_at_the_limit_and_explains_no_eligible_lane():
    lane = Lane("a", "deepseek", "m")
    held = D.place(Item("x"), (lane,), World(), W, D.Slots(in_flight=4, limit=4), B)
    assert held.status == D.HOLD
    none = D.place(Item("x", min_trust=9), (lane,), World(), W, FREE, B)
    assert none.status == D.NONE
    assert any("trust tier" in w for w in none.why)


def _decision(**kw):
    base = {
        "item": "x",
        "lane": "l",
        "ok": True,
        "score": 1.0,
        "parts": (("fit", 1.0),),
        "reasons": (),
    }
    return Decision(**{**base, **kw})


def test_the_note_is_one_line_within_its_bound():
    d = _decision(reasons=("line one\nline two", "z" * 900), family="f", model="m")
    text = D.note(d, B)
    assert "\n" not in text
    assert "line one line two" in text
    assert len(text) <= B.note_chars
    assert text.endswith("...")


@pytest.mark.parametrize("chars", [1, 2, 3])
def test_the_note_bound_holds_below_the_cut_mark(chars):
    d = _decision(family="f", model="m")
    assert len(D.note(d, D.Bounds(note_chars=chars, why_items=1))) == chars


def test_the_note_keeps_every_figure_when_only_the_why_is_cut():
    d = _decision(reasons=("z" * 900,), family="f", model="m")
    assert "fit=1.000" in D.note(d, D.Bounds(note_chars=120, why_items=3))


def _catalog():
    return modelreg.load()


def test_catalog_facts_read_the_family_and_the_vendor():
    cat = _catalog()
    t = replace(TPL, family="qwen", model="Qwen3-Coder", route="ollama")
    facts = D.catalog_facts(cat, t)
    assert (facts.model, facts.family, facts.vendor) == ("Qwen3-Coder", "qwen", "alibaba")


def test_catalog_facts_take_the_route_model_when_the_template_names_none():
    t = replace(TPL, family="deepseek", model="", route="deepseek")
    assert D.catalog_facts(_catalog(), t).model == "deepseek-chat"


def test_a_model_the_catalog_does_not_know_is_unavailable_whatever_the_template_says():
    t = replace(TPL, family="deepseek", model="house-build-7", route="ollama")
    facts = D.catalog_facts(_catalog(), t)
    assert (facts.family, facts.vendor) == ("unknown", "")
    got = _pull(src=_src(template=t, facts=facts))
    assert got.status == D.UNAVAILABLE
    assert any("no family for model 'house-build-7'" in w for w in got.why)


def test_lane_of_prefers_template_prices_and_keeps_the_catalog_window():
    src = _src(
        template=replace(TPL, price_in=2.0, kinds=("task",), trust=2, local=True),
        facts=replace(FACTS, price_in=9.0, price_out=4.0, window=1000),
        live=1,
        cap=2,
    )
    lane = D.lane_of("x", src)
    assert (lane.price_in, lane.price_out, lane.window) == (2.0, 4.0, 1000)
    assert (lane.trust, lane.kinds, lane.local, lane.live, lane.cap) == (
        2,
        ("task",),
        True,
        1,
        2,
    )


def _state(*items):
    st = State()
    for it in items:
        st.items[it.id] = it
    return st


def _q(iid, **kw):
    return QItem(id=iid, kind=kw.pop("kind", "task"), title=iid, **kw)


def test_item_of_carries_chain_wait_and_the_ledger_kind():
    st = _state(_q("a", globs=["src/a.py"], needs=["n"], tags=["kind:security"]))
    got = D.item_of(st, "a", chain=("a",), waited={"a": 60.0}, shared=[])
    assert got == Item(
        "a", kind="security", globs=("src/a.py",), needs=("n",), critical=1.0, waited_s=60.0
    )
    assert D.item_of(st, "a", chain=(), waited={}, shared=[]).critical == 0.0


def test_a_shared_glob_never_refuses_an_item():
    st = _state(_q("a", globs=["CHANGELOG.md", "src/a.py"]))
    assert D.item_of(st, "a", chain=(), waited={}, shared=["CHANGELOG.md"]).globs == ("src/a.py",)


NOW = 1_000_000.0


def _lease(holder, globs, *, at, **kw):
    return Lease(holder=holder, acquired_at=at, renewed_at=at, ttl_s=60, globs=globs, **kw)


def test_world_of_reads_live_leases_and_the_scheduler_dependency_verdict():
    cfg = Config()
    st = _state(
        _q("done-dep", state=DONE),
        _q("open-dep"),
        _q("held", lease=_lease("x", ["src/a.py", "CHANGELOG.md"], at=NOW - 10)),
        _q("stale", lease=_lease("y", ["src/b.py"], at=0.0)),
    )
    items = (Item("a", needs=("done-dep", "open-dep")),)
    world = D.world_of(
        st,
        items,
        cfg=cfg,
        now=NOW,
        ledger=L.Ledger({}),
        families={"held": "deepseek"},
        shared=["CHANGELOG.md"],
    )
    assert world.done == frozenset({"done-dep"})
    assert world.live == (Live("held", ("src/a.py",), "deepseek"),)
    assert world.reviewer_families is None


# -- `ddflow next --lane`: the reader over the pure answer ------------------------------

TEMPLATES = """
[template.ds]
harness = "kilo"
route = "deepseek"
family = "deepseek"
trust = 1
budget_usd = 1.0

[template.notrust]
harness = "kilo"
route = "deepseek"
family = "deepseek"
budget_usd = 1.0
"""


def _approve(repo, name):
    templates, _ = T.load(repo / ".ddflow/local/lane-templates.toml")
    T.approve(EventLog(repo, "operator"), templates[name])


def _task(repo, iid):
    run_cli(
        repo, "task", "add", iid, "--title", f"{iid} task", "--phase", "P1", "--globs", f"{iid}.py"
    )


def _queue(repo, tmp=TEMPLATES):
    path = repo / ".ddflow/local/lane-templates.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tmp, encoding="utf-8")
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "P")
    _task(repo, "a")


def _events(repo):
    return sum(1 for _ in EventLog(repo, "reader").read_all())


def test_next_lane_offers_with_a_note_and_writes_nothing(repo):
    _queue(repo)
    _approve(repo, "ds")
    before = _events(repo)
    out = api.next_lane(repo, lane="ds")
    assert out.exit == 0, out
    assert (out.data["status"], out.data["item"]) == (D.OFFER, "a")
    assert out.data["note"].startswith(
        "dispatch item=a lane=ds family=deepseek model=deepseek-chat"
    )
    assert _events(repo) == before


def test_next_lane_without_a_template_is_unavailable(repo):
    _queue(repo)
    out = api.next_lane(repo, lane="hand-run")
    assert out.exit == 2
    assert out.data["status"] == D.UNAVAILABLE
    assert out.data["item"] == ""
    assert "no template" in out.reason


def test_next_lane_needs_a_declared_trust_and_an_approval(repo):
    _queue(repo)
    out = api.next_lane(repo, lane="notrust")
    assert out.data["status"] == D.UNAVAILABLE
    assert "declares no trust" in out.reason
    assert "not approved" in out.reason


def test_next_lane_holds_at_the_parallel_limit(repo):
    _queue(repo)
    _approve(repo, "ds")
    cfg_file = repo / ".ddflow/local/config.toml"
    cfg_file.write_text(
        '[schedule]\nparallel = "fixed"\nmax_parallel_tasks = 1\n', encoding="utf-8"
    )
    _task(repo, "b")
    assert api.claim(repo, "b", agent="other").exit == 0
    out = api.next_lane(repo, lane="ds")
    assert out.exit == 2
    assert out.data["status"] == D.HOLD
    assert "parallel limit" in out.reason


def test_next_lane_leaves_a_reserved_slot_to_reserved_work(repo):
    _queue(repo)
    _approve(repo, "ds")
    (repo / ".ddflow/local/config.toml").write_text(
        '[schedule]\nparallel = "fixed"\nmax_parallel_tasks = 2\nreserved_slots = 1\n',
        encoding="utf-8",
    )
    _task(repo, "b")
    assert api.claim(repo, "b", agent="other").exit == 0
    out = api.next_lane(repo, lane="ds")
    assert out.exit == 2
    assert (out.data["status"], out.data["item"]) == (D.NONE, "")


def test_next_lane_judges_the_cap_plan_counts_with_a_worktree_limit(repo):
    _queue(repo)
    _approve(repo, "ds")
    (repo / ".ddflow/local/config.toml").write_text(
        '[schedule]\nparallel = "fixed"\nmax_parallel_tasks = 4\n[worktree]\nmax_parallel = 1\n',
        encoding="utf-8",
    )
    _task(repo, "b")
    assert api.claim(repo, "b", agent="other").exit == 0
    out = api.next_lane(repo, lane="ds")
    assert out.data["status"] == D.HOLD
    assert "1 in flight of 1" in out.reason


def test_next_lane_counts_a_holder_of_its_family_against_the_family_cap(repo):
    _queue(repo)
    _approve(repo, "ds")
    (repo / ".ddflow/local/config.toml").write_text("[swarm]\nfamily_cap = 1\n", encoding="utf-8")
    _task(repo, "b")
    run_cli(repo, "--agent", "other", "session", "start", "--model", "deepseek-chat")
    assert api.claim(repo, "b", agent="other").exit == 0
    out = api.next_lane(repo, lane="ds")
    assert out.data["status"] == D.NONE
    assert "family_cap" in out.reason


def test_next_lane_leaves_an_interrupted_item_to_be_salvaged(repo):
    _queue(repo)
    st = fold(EventLog(repo, "reader").read_all(), strict=False)
    st.items["a"].state = RUNNING
    plan = SimpleNamespace(ready=[st.items["a"]], capped=[], overlapped=[])
    cfg = Config()
    assert PL._candidates(plan, st, cfg, caps(st, cfg, {}), {}) == []


def test_next_lane_is_the_cli_flag_and_prints_the_claim(repo):
    _queue(repo)
    _approve(repo, "ds")
    got = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "next", "--lane", "ds"],
        capture_output=True,
        text=True,
        env={**os.environ, "DDFLOW_AGENT": "lane"},
    )
    assert got.returncode == 0, got.stderr
    assert "ds: take a" in got.stdout
    assert "ddflow claim a --note" in got.stdout


def test_the_printed_claim_quotes_the_item():
    out = SimpleNamespace(exit=0, data={"lane": "l", "item": "a b;x", "note": "n"})
    text = NAV._next_text(out, {"lane": "l"}, None)
    assert "ddflow claim 'a b;x' --note n" in text


def test_next_lane_over_mcp_is_the_same_answer(repo):
    _queue(repo)
    _approve(repo, "ds")
    reply = Server(repo, agent="lane").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_next", "arguments": {"lane": "ds"}},
        }
    )
    body = json.loads(reply["result"]["content"][0]["text"])
    assert (body["status"], body["item"]) == (D.OFFER, "a")
    assert body["note"] == api.next_lane(repo, lane="ds").data["note"]


def test_next_without_a_lane_is_the_queue_answer(repo):
    _queue(repo)
    out = api.next_item(repo)
    assert "status" not in out.data
    assert [r["id"] for r in out.data["ready"]] == ["a"]
