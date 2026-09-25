"""The ten defects an adversarial rubber-duck pass found in the b3981975 snapshot.

Each one arrived with a runnable probe against a pristine export of that commit, and
each is reproduced here as the regression test. Grouped by the class they belong to,
because the classes repeat and the grouping is what makes the next instance findable:

* **provenance loss** — an event kind the reconstruction path never reads (1)
* **non-atomic multi-write** — a command that validates as it writes (2)
* **hierarchy half-applied** — a rule written for phases that sub-tasks also need (3)
* **missing existence check** — the one mutating command that could invent an item (4)
* **vacuous truth** — a requirement that disappears instead of raising (5)
* **fold reorder** — a handler that replaces where its siblings merge (6)
* **scope mismatch** — a global cap enforced against a filtered count (7)
* **stale finding** — a detector that keeps firing on work nobody is doing (8)
* **two implementations of one rule** — a threshold that differs by backend (9)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _queue(repo, *, tasks=("P1.T1",)):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "core", "--globs", "core/**")
    for t in tasks:
        run_cli(repo, "task", "add", t, "--phase", "P1", "--globs", f"core/{t}.py")


# -- 1. provenance: replay must carry architectural decisions -------------------------


def test_replay_carries_decisions_and_their_reversals(repo):
    """`replay` is the rebuild-from-the-log path, and it dropped every decision.

    `PROVENANCE_KINDS` — the filter — listed sessions, research, lessons, phases and
    tasks but neither `decision.recorded` nor `decision.superseded`, so the two
    decision renderers registered in `_REPLAY_RENDERERS` were unreachable. Live
    decisions still appeared in the brief, which reads folded state, so the loss was
    invisible everywhere except the one place it mattered: a rebuild that has nothing
    but the log. Everything about a *superseded* decision — its context, its rejected
    alternatives, why it was reversed — existed nowhere at all.
    """
    _queue(repo)
    run_cli(
        repo,
        "decision",
        "add",
        "--id",
        "D1",
        "--title",
        "SQLite as the store",
        "--decision",
        "keep state in SQLite",
        "--alternatives",
        "rejected: Postgres",
    )
    run_cli(
        repo,
        "decision",
        "add",
        "--id",
        "D2",
        "--title",
        "the log is the store",
        "--decision",
        "event log is authoritative; SQLite is a disposable index",
        "--supersedes",
        "D1",
    )
    code, out, err = run_cli(repo, "replay")
    assert code == OK, err
    assert "SQLite as the store" in out, f"D1 is missing from the reconstruction:\n{out}"
    assert "the log is the store" in out, f"D2 is missing from the reconstruction:\n{out}"
    assert "Postgres" in out, (
        "the rejected alternative is the part a rebuild most needs — it is why the "
        f"obvious design was not taken:\n{out}"
    )


def test_every_replay_renderer_has_a_provenance_kind(repo):
    """The ratchet: a renderer with no kind is dead code that looks like a feature."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ddflow.infra.log import PROVENANCE_KINDS
    from ddflow.services.sessions import _REPLAY_RENDERERS

    orphans = set(_REPLAY_RENDERERS) - set(PROVENANCE_KINDS) - {"item.completed"}
    assert not orphans, (
        f"{sorted(orphans)} render into the reconstruction but are filtered out "
        f"before they get there, so the renderer can never run"
    )


# -- 2. split validates everything before it writes anything --------------------------


def test_a_refused_split_leaves_no_half_made_children(repo):
    _queue(repo, tasks=("P1.T1", "P1.T2"))
    code, _, err = run_cli(
        repo, "split", "P1.T1", "--into", "P1.T1a=first", "--into", "P1.T2=collides"
    )
    assert code == FAIL and "P1.T2" in err, err
    _code, out, _ = run_cli(repo, "--json", "next")
    ids = {r["id"] for r in json.loads(out)["ready"]}
    assert "P1.T1a" not in ids, (
        "the first child was written before the second was validated, so a refused "
        "split left the parent an umbrella nobody asked for — un-claimable because it "
        f"now has a child, un-completable because that child is open. ready={ids}"
    )
    assert "P1.T1" in ids, "and the parent must still be ordinary, claimable work"


def test_a_split_cannot_give_two_children_the_same_id(repo):
    _queue(repo)
    code, out, err = run_cli(repo, "split", "P1.T1", "--into", "X=one", "--into", "X=two")
    assert code == FAIL, (
        f"both were appended, `fold` merged them into one item whose title was "
        f"silently the second spec's, and the command reported two children: {out}"
    )
    assert "twice" in err.lower() or "own id" in err.lower(), err


# -- 3. the hierarchy rule applies to any parent, not only to phases ------------------


def test_removing_a_task_umbrella_does_not_orphan_its_sub_tasks(repo):
    _queue(repo)
    run_cli(repo, "task", "add", "P1.T1.a", "--parent", "P1.T1", "--globs", "core/a.py")
    code, _, err = run_cli(repo, "remove", "P1.T1")
    assert code == REFUSED, (
        "the child check ran only for phases, so removing a task umbrella left its "
        "children live but unreachable: `State.tasks(phase)` walks descendants and "
        f"`children()` skips a removed node, so the phase went quiet. exit={code}"
    )
    assert "P1.T1.a" in err, err


# -- 4. block: a typo must not become an item, and a blocked item is not ready --------


def test_block_refuses_an_unknown_id(repo):
    _queue(repo)
    code, _, err = run_cli(repo, "block", "P1.TYPO", "--reason", "waiting on the vendor")
    assert code == FAIL, (
        "`fold` reaches items through `_item()`, which CREATES one for an unknown id, "
        "so this was the only mutating command where a typo materialised a titleless "
        "phantom task — which the scheduler then offered as the next thing to do."
    )
    assert "no such item" in err, err
    _code, out, _ = run_cli(repo, "--json", "next")
    assert "P1.TYPO" not in out, out


def test_a_blocked_item_is_not_offered_as_ready(repo):
    _queue(repo)
    run_cli(repo, "block", "P1.T1", "--reason", "waiting on an operator decision")
    _code, out, _ = run_cli(repo, "--json", "next")
    plan = json.loads(out)
    assert "P1.T1" not in {r["id"] for r in plan["ready"]}, (
        f"the scheduler never looked at `state == blocked`, so an item parked on an "
        f"external decision was handed straight back to an agent: {plan}"
    )
    blocked = {b["item"]: b for b in plan["blocked"]}
    assert "operator decision" in blocked["P1.T1"]["detail"], blocked["P1.T1"]


# -- 5. a requirement that names nothing must be loud, not satisfied ------------------


def test_a_required_gate_absent_from_every_pipeline_blocks_completion(repo):
    """The vacuous-truth class aimed squarely at the anti-vacuous-pass mechanism.

    `required` is enforced by intersecting it with the item's pipeline. Naming a gate
    that no pipeline lists therefore makes the requirement *disappear* rather than
    raise: an operator who sets `required = ["critic"]` and trims `critic` out of
    `task_pipeline` gets a project where nothing at all is required, reported as fully
    compliant.
    """
    _queue(repo)
    (repo / ".ddflow" / "config.toml").write_text(
        '[gates]\nrequired = ["critic"]\ntask_pipeline = ["implement", "unit_tests", "merge"]\n'
    )
    for g in ("implement", "unit_tests", "merge"):
        run_cli(repo, "gate", "record", "P1.T1", g, "--outcome", "passed", "--evidence", "ok")
    code, out, err = run_cli(repo, "complete", "P1.T1", "--model", "claude-opus-5")
    assert code == REFUSED, f"completed with zero gates enforced:\n{out}"
    assert "critic" in err and "enforces nothing" in err, err


def test_silence_on_a_pipeline_gate_is_not_a_pass(repo):
    """Six of the operator's ten steps could be omitted without a trace.

    `required` held only implement/unit_tests/merge, so research, the rubber-duck, the
    critic, the standards pass, the bug hunt and the dedupe check could each be left
    un-run and un-skipped and the task completed clean. An explicit
    `gate skip --reason` is still an outcome, so the escape hatch stays auditable.
    """
    _queue(repo)
    for g in ("implement", "unit_tests", "merge"):
        run_cli(repo, "gate", "record", "P1.T1", g, "--outcome", "passed", "--evidence", "ok")
    code, _, err = run_cli(repo, "complete", "P1.T1", "--model", "claude-opus-5")
    assert code == REFUSED, err
    for gate in ("research", "rubber_duck", "critic", "standards", "bug_hunt", "dedupe"):
        assert gate in err, f"{gate} was omitted without complaint:\n{err}"

    for gate in ("research", "rules", "standards", "bug_hunt", "dedupe"):
        assert run_cli(repo, "gate", "skip", "P1.T1", gate, "--reason", "n/a here")[0] == OK
    for gate in ("rubber_duck", "critic"):
        run_cli(
            repo,
            "gate",
            "record",
            "P1.T1",
            gate,
            "--outcome",
            "passed",
            "--evidence",
            "ok",
            "--model",
            "gemini-2.5-pro",
        )
    code, _, err = run_cli(repo, "complete", "P1.T1", "--model", "claude-opus-5")
    assert code == OK, f"an explicitly-skipped gate is an outcome and must unblock:\n{err}"


def test_recording_a_gate_out_of_order_says_so(repo):
    _queue(repo)
    code, _out, err = run_cli(
        repo,
        "gate",
        "record",
        "P1.T1",
        "rubber_duck",
        "--outcome",
        "passed",
        "--evidence",
        "ok",
        "--model",
        "gemini-2.5-pro",
    )
    assert code == OK, "'warn' is the default: it reports, it does not refuse"
    assert "implement" in err, (
        f"a rubber-duck review recorded before `implement` reviewed an empty diff: {err}"
    )


# -- 6. fold handlers merge; they never replace ---------------------------------------


def test_a_re_recorded_lesson_stays_superseded(repo, log):
    """`_h_decision` documents this hazard and guards it; its sibling did not."""
    from ddflow.core.model import fold

    log.append("lesson.recorded", "L1", {"title": "old", "rule": "do the old thing"})
    log.append(
        "lesson.recorded", "L2", {"title": "new", "rule": "do the new thing", "supersedes": ["L1"]}
    )
    log.append(
        "lesson.recorded", "L1", {"title": "old", "rule": "do the old thing", "seen_in": ["P1.T9"]}
    )
    st = fold(log.read_all(), strict=False)
    assert st.lessons["L1"].superseded_by == "L2", (
        "a re-record rebuilt the Lesson wholesale, so advice the project had "
        "explicitly replaced walked back into the reconstruction brief and into "
        "`recall`, presented as current"
    )


def test_a_re_recorded_decision_stays_superseded(repo, log):
    from ddflow.core.model import fold

    log.append("decision.recorded", "D1", {"title": "a", "decision": "use A"})
    log.append("decision.superseded", "D1", {"by": "D2"})
    log.append("decision.recorded", "D1", {"title": "a", "decision": "use A"})
    st = fold(log.read_all(), strict=False)
    assert st.decisions["D1"].superseded_by == "D2"
    assert st.decisions["D1"].status == "superseded", (
        "`status` and `superseded_by` are one fact stored twice; the re-record carried "
        "the default status='accepted' and the reconstruction presented a reversed "
        "decision as the one in force"
    )


# -- 7. a global cap must be measured globally ----------------------------------------


def _two_phases(repo, config: str):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(config)
    run_cli(repo, "phase", "add", "P1", "--globs", "p1/**")
    run_cli(repo, "phase", "add", "P2", "--globs", "p2/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "p1/a.py")
    run_cli(repo, "task", "add", "P2.T1", "--phase", "P2", "--globs", "p2/a.py")


def _ready_in(repo, phase: str, agent: str) -> list[str]:
    _code, out, _ = run_cli(repo, "--json", "next", "--phase", phase, agent=agent)
    return [r["id"] for r in json.loads(out)["ready"]]


SCOPE = (
    "`p.running` holds only candidates from the queried phase, so the cap was applied "
    "against a count of zero: two agents each asking about their own phase were both "
    "told to go ahead. ready={}"
)


def test_the_in_flight_cap_counts_the_whole_queue_not_one_phase(repo):
    _two_phases(repo, "[schedule]\nmax_parallel_tasks = 1\n")
    run_cli(repo, "claim", "P1.T1", "--no-worktree", agent="alpha")

    ready = _ready_in(repo, "P2", "beta")
    assert not ready, SCOPE.format(ready)


def test_the_worktree_cap_counts_the_whole_queue_not_one_phase(repo):
    _two_phases(repo, "[worktree]\nmax_parallel = 1\n")
    run_cli(repo, "claim", "P1.T1", agent="alpha")

    ready = _ready_in(repo, "P2", "beta")
    assert not ready, SCOPE.format(ready)


def test_a_lease_that_made_no_worktree_does_not_consume_a_worktree_slot(repo):
    """The two knobs are two statements, and `min()` of them was neither.

    `worktree.max_parallel` is a claim about this machine's disk and CPU. A
    `--no-worktree` lease -- a review, a research task -- creates no tree and consumes
    neither, so counting it there is a silent knob drop: an operator who allowed four
    in flight and one tree got one in flight, and nothing said so.
    """
    _two_phases(repo, "[worktree]\nmax_parallel = 1\n[schedule]\nmax_parallel_tasks = 4\n")
    run_cli(repo, "claim", "P1.T1", "--no-worktree", agent="alpha")

    assert _ready_in(repo, "P2", "beta") == ["P2.T1"], (
        "no worktree exists, so the worktree cap has nothing to cap"
    )


# -- 8. a finding about removed work is a finding nobody can act on -------------------


def test_loop_detection_ignores_items_removed_from_the_queue(repo):
    _queue(repo)
    for _ in range(4):
        run_cli(repo, "claim", "P1.T1", "--no-worktree")
        run_cli(repo, "release", "P1.T1")
    assert run_cli(repo, "loops")[0] == FAIL, "the thrash itself must still be reported"

    run_cli(repo, "remove", "P1.T1", "--reason", "dropped")
    code, out, _ = run_cli(repo, "loops")
    assert code == NOTHING, (
        "the remedy the finding suggests — abandon it — had already been done in a "
        f"stronger form, and with `on_detect = 'block'` this is a permanent block on "
        f"work nobody is doing:\n{out}"
    )


# -- 9. one rule, one implementation --------------------------------------------------


def test_both_search_backends_agree_on_the_shortest_usable_term(repo):
    """The FTS5 tokenizer kept terms >= 2 chars; the LIKE fallback kept > 2.

    Same query, two answers, decided by whether the local SQLite was built with FTS5 —
    a build flag nobody sets deliberately and no test would otherwise vary.
    """
    from ddflow.infra import store as S

    src = Path(S.__file__).read_text("utf-8")
    comparisons = [ln.strip() for ln in src.splitlines() if "MIN_TERM_CHARS" in ln and "len(" in ln]
    assert len(comparisons) >= 2, comparisons
    assert all(">= MIN_TERM_CHARS" in ln for ln in comparisons), (
        f"the two term filters must use the same comparison: {comparisons}"
    )


# -- 10. fold survives an event that would make an item its own ancestor --------------


def test_fold_refuses_to_make_an_item_its_own_ancestor(repo, log):
    """`fold` is the one function fed arbitrary JSON from disk; it may not be broken
    by it. A self-parented item is its own open descendant, hence permanently an
    umbrella: never offered, never claimable, never completable."""
    from ddflow.core.model import fold

    log.append("task.added", "T1", {"title": "t1"})
    log.append("task.updated", "T1", {"parent": "T1"})
    st = fold(log.read_all(), strict=False)
    assert st.items["T1"].parent != "T1", "self-parentage must be dropped, not stored"
    assert not st.open_descendants("T1"), "and the item must not become its own umbrella"

    log.append("task.added", "T2", {"title": "t2", "parent": "T1"})
    log.append("task.updated", "T1", {"parent": "T2"})
    st = fold(log.read_all(), strict=False)
    assert st.items["T1"].parent != "T2", "a two-step parent cycle is the same defect"


# -- 11. an unidentified reviewer establishes nothing ---------------------------------


def test_a_reviewer_with_no_model_cannot_satisfy_independence(repo):
    """The vacuous-pass class aimed at the anti-vacuous-pass mechanism itself.

    `gates.record` defaults `by` to the agent id, and `family_of` used to return the
    unmatched name back — so a `standards` gate recorded with no `--model` arrived as
    family "host-12345", compared unequal to the author's "anthropic", and SATISFIED
    the different-family requirement on its own. The check whose entire purpose is to
    refuse unverified independence was passed by the absence of information.

    Found while writing a different test, which is the usual way: the fixture that was
    supposed to set up a refusal produced a pass.
    """
    _queue(repo)
    for g in ("research", "rules", "implement", "unit_tests", "bug_hunt", "dedupe", "merge"):
        run_cli(repo, "gate", "record", "P1.T1", g, "--outcome", "passed", "--evidence", "ok")
    # A reviewer that ran, passed, and named no model.
    run_cli(
        repo,
        "gate",
        "record",
        "P1.T1",
        "standards",
        "--outcome",
        "passed",
        "--evidence",
        "roborev: no findings",
    )
    run_cli(
        repo,
        "gate",
        "record",
        "P1.T1",
        "rubber_duck",
        "--outcome",
        "passed",
        "--evidence",
        "looks fine",
        "--model",
        "claude-sonnet-5",
    )
    run_cli(
        repo,
        "gate",
        "record",
        "P1.T1",
        "critic",
        "--outcome",
        "passed",
        "--evidence",
        "no findings",
        "--model",
        "claude-opus-5",
    )

    code, _, err = run_cli(repo, "complete", "P1.T1", "--model", "claude-opus-5")
    assert code == REFUSED, "every identified reviewer was the author's own family"
    assert "independence" in err.lower(), err
    assert "standards" in err, f"and it must say the anonymous one proved nothing: {err}"


def test_one_family_map_serves_both_the_reviewer_and_the_gate(repo):
    """Two implementations of one rule, with different entries and different answers.

    `reviewer.family_of` carried 23 substrings and returned the model's own name for an
    unknown; `gates.family_of` carried 9 and returned "unknown". So a Qwen reviewer was
    "alibaba" to one and "alibaba" to the other by luck, while a Phi reviewer was
    "microsoft" to the reviewer layer and "phi-4" to the gate that decides whether the
    review counted. Same drift class as the two that preceded it here.
    """
    from ddflow.config import FAMILY_HINTS, Config
    from ddflow.services.gates import family_of as gate_family
    from ddflow.services.review import family_of as reviewer_family

    cfg = Config.load()
    assert cfg.agent.families == FAMILY_HINTS, "the config default IS the canonical map"
    for model in ("phi-4", "qwen3-coder", "claude-opus-5", "glm-4.6", "totally-made-up"):
        assert gate_family(model, cfg) == reviewer_family(model), model


# -- 12. the MCP boundary drops nothing in silence ------------------------------------


def _mcp(repo, calls):
    """Drive a real server over a real pipe. Returns the parsed replies."""
    import os
    import subprocess as sp

    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root), "DDFLOW_AGENT": "mcp-test"}
    proc = sp.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "mcp"],
        stdin=sp.PIPE,
        stdout=sp.PIPE,
        stderr=sp.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    out = []
    try:
        for rid, (method, params) in enumerate(calls, 1):
            msg = {"jsonrpc": "2.0", "method": method, "params": params}
            if method != "notifications/initialized":
                msg["id"] = rid
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            if method != "notifications/initialized":
                out.append(json.loads(proc.stdout.readline()))
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
    return out


def test_an_unknown_tool_argument_is_refused_not_ignored(repo):
    """Every tool schema declared `additionalProperties: false`; nothing enforced it.

    So a caller passing an argument the tool does not have got a SUCCESS and a
    different result than it asked for. Concretely: `ddflow_decision_add` had no `id`
    property, an agent passing `id="D1"` had it dropped, the decision landed under a
    generated id, and the `supersedes: ["D1"]` written moments later referenced
    nothing. An agent cannot see this — it has only the reply, and the reply said OK.
    """
    run_cli(repo, "init")
    replies = _mcp(
        repo,
        [
            (
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            ),
            ("notifications/initialized", {}),
            ("tools/call", {"name": "ddflow_status", "arguments": {"nonsense": "x"}}),
        ],
    )
    body = replies[-1]["result"]["content"][0]["text"]
    assert replies[-1]["result"]["isError"], f"an unknown argument must be an error: {body}"
    assert "nonsense" in body and "Known:" in body, body


def test_a_caller_chosen_id_survives_the_mcp_boundary(repo):
    """The three record-keeping tools must accept the id the caller will cite later."""
    run_cli(repo, "init")
    replies = _mcp(
        repo,
        [
            (
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            ),
            ("notifications/initialized", {}),
            (
                "tools/call",
                {
                    "name": "ddflow_decision_add",
                    "arguments": {
                        "id": "D1",
                        "title": "money is int",
                        "decision": "int minor units",
                    },
                },
            ),
            (
                "tools/call",
                {
                    "name": "ddflow_lesson_add",
                    "arguments": {
                        "id": "L1",
                        "title": "empty collections satisfy checks about their contents",
                    },
                },
            ),
            (
                "tools/call",
                {
                    "name": "ddflow_decision_add",
                    "arguments": {
                        "id": "D2",
                        "title": "and then it was not",
                        "decision": "use Decimal",
                        "supersedes": "D1",
                    },
                },
            ),
        ],
    )
    for r in replies[2:]:
        assert not r["result"].get("isError"), r["result"]["content"][0]["text"]

    # `--all`, because D1 is retired by D2 and the default list shows only what is in
    # force — which is right, and is also why the history has to be reachable.
    _code, out, _ = run_cli(repo, "--json", "decision", "list", "--all")
    ids = {d["id"]: d for d in json.loads(out)}
    assert {"D1", "D2"} <= set(ids), f"generated ids replaced the chosen ones: {sorted(ids)}"
    assert ids["D1"]["superseded_by"] == "D2", (
        f"the supersession referenced the id the caller chose, so it must resolve: {ids['D1']}"
    )
    _code, out, _ = run_cli(repo, "--json", "lesson", "search", "empty collections")
    assert "L1" in out, out


# -- 13. "not supplied" and "supplied as empty" are different things ------------------


def test_a_dependency_can_be_CLEARED_over_mcp(repo):
    """Over MCP a dependency could be added and never removed.

    The argv builder treated an empty string as "the caller did not supply this", so
    `ddflow_update(id="X.A", needs="")` returned success and changed nothing, while
    `ddflow update X.A --needs ""` cleared it. Breaking a cycle is precisely the
    operation that needs an empty value — and it is the operation `ddflow loops` tells
    you to perform, so the tool's own advice was unfollowable from the surface it was
    delivered on.
    """
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "PX", "--globs", "x/**")
    for tid, needs in (("X.A", "X.C"), ("X.B", "X.A"), ("X.C", "X.B")):
        run_cli(repo, "task", "add", tid, "--phase", "PX", "--needs", needs, "--globs", f"x/{tid}")
    assert run_cli(repo, "loops")[0] == FAIL, "the ring must be reported first"

    replies = _mcp(
        repo,
        [
            (
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            ),
            ("notifications/initialized", {}),
            ("tools/call", {"name": "ddflow_update", "arguments": {"id": "X.A", "needs": ""}}),
        ],
    )
    assert not replies[-1]["result"].get("isError"), replies[-1]

    _code, out, _ = run_cli(repo, "--json", "show", "X.A")
    assert json.loads(out)["needs"] == [], f"the edge is still there: {out}"
    assert run_cli(repo, "loops")[0] == NOTHING, "breaking one edge breaks the ring"


def test_an_omitted_field_is_still_left_alone(repo):
    """The over-correction to avoid: treating every absent key as a clear.

    A client that sends only the field it is changing must not wipe the others, and one
    that fills every optional property with "" is why `clearable` WAS opt-in per tool
    rather than the global rule, back when the argv path needed it. `ddflow_update` now
    goes through the typed layer, where absent and empty are simply different values;
    the behaviour this test pins is unchanged.
    """
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "core/**")
    run_cli(
        repo,
        "task",
        "add",
        "T1",
        "--phase",
        "P1",
        "--globs",
        "core/a.py",
        "--title",
        "keep me",
        "--tags",
        "alpha",
    )
    replies = _mcp(
        repo,
        [
            (
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            ),
            ("notifications/initialized", {}),
            (
                "tools/call",
                {"name": "ddflow_update", "arguments": {"id": "T1", "body": "new body"}},
            ),
        ],
    )
    assert not replies[-1]["result"].get("isError"), replies[-1]
    item = json.loads(run_cli(repo, "--json", "show", "T1")[1])
    assert item["body"] == "new body"
    assert item["title"] == "keep me", item
    assert item["globs"] == ["core/a.py"], item
    assert item["tags"] == ["alpha"], item


# -- 11. a fingerprint read in two passes is not a fingerprint -------------------------


def test_head_observes_each_shard_exactly_once(repo, monkeypatch):
    """`head()` used to stat every shard, then walk them all AGAIN for the clock.

    An append landing between the two walks, carrying a Lamport value at or below the
    current high — a second agent whose clock is behind, which is the ordinary case on
    a shared mount — was invisible in BOTH components: the size walk had already read
    that shard, and the tail walk saw no higher Lamport. The fingerprint came back
    byte-identical to the pre-append one, `Store.stale` said "current", and the index
    served an answer that was missing events.

    That is exactly the interleaving the byte count exists to catch, defeated by the
    read order. Asserting the walk count rather than racing two processes: the defect
    IS the second walk, and a scheduler-dependent race is a test that passes on the
    machine that has the bug.
    """
    from ddflow.infra.log import EventLog

    log = EventLog(repo, "agent-a")
    log.append("session.started", "s1", {})

    walks = []
    original = EventLog.shards
    monkeypatch.setattr(
        EventLog, "shards", lambda self: (walks.append(1), original(self))[1], raising=True
    )
    log.head()
    assert len(walks) == 1, (
        f"the size and the clock are read in {len(walks)} separate walks over the "
        f"shards, so an append landing between them is missed by both"
    )


def test_a_second_agents_append_changes_the_fingerprint_even_with_a_lower_clock(repo):
    """The byte component's whole job, stated as behaviour.

    A shard written by an agent whose Lamport clock is BEHIND the global high moves no
    high-water mark — only the byte count can see it.
    """
    from ddflow.infra.log import EventLog

    log = EventLog(repo, "agent-a")
    for _ in range(5):
        log.append("session.started", "s1", {})
    before = log.head()

    stale_line = json.dumps(
        {"kind": "session.note", "subject": "s1", "lamport": 1, "agent": "agent-b", "data": {}}
    )
    (log.dir / "agent-b.jsonl").write_text(stale_line + "\n")

    after = log.head()
    assert after != before, (
        f"a whole shard appeared and the fingerprint did not move: {before} == {after}"
    )
