"""RA-policy: standing approval rules ship OFF, approve only their own verb within a cap, and are
recorded as policy -- never as a person (D-RA-4, D-RA-Q8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import clock
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import actors as A
from ddflow.services import approval as AP
from ddflow.services.approvals import audit as AU
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import gate_skip as GS
from ddflow.services.approvals import policy as PO
from ddflow.services.approvals.canon import Canon
from ddflow.services.ask import engine as E


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "t.py")
    for _ in range(3):
        assert run_cli(repo, "gate", "record", "T1", "critic", "--outcome", "unavailable",
                       "--reason", "reviewer down")[0] == 0  # fmt: skip
    assert (
        run_cli(repo, "gate", "record", "T1", "critic", "--outcome", "failed", "--reason", "f")[0]
        == 0
    )
    return repo


def _state(repo):
    return fold(EventLog(repo, "bot").read_all(), strict=False)


def _canon(repo, log):
    it = _state(repo).items["T1"]
    why = "failed with no triaged findings on record"
    return GS.canon_for(it, "critic", "T1", why, log.agent_id, "skipped")


def _cfg(**over):
    cfg = Config()
    for k, v in over.items():
        setattr(cfg.approvals, k, v)
    return cfg


def _record(repo, gate, outcome, reason=""):
    return run_cli(
        repo,
        "gate",
        "record",
        "T1",
        gate,
        "--outcome",
        outcome,
        *(["--reason", reason] if reason else []),
    )[0]


def _only_unavailable_gate(repo, *outcomes):
    """Record a "merged" T1 whose ONLY unmet required gate is `unit_tests`: the other two
    required gates (implement, merge) pass, and `unit_tests` gets each named `outcome` in
    turn. ``outcomes = ("unavailable",)`` (or ``("partial",)``) is exactly what the standing
    `complete_unavailable` predicate answers; ``("failed",)`` is NEVER answered."""
    assert _record(repo, "implement", "passed") == 0
    assert _record(repo, "merge", "passed") == 0
    for outcome in outcomes:
        why = (
            "worktree gone before it could run"
            if outcome == "unavailable"
            else (
                "worktree gone after the merge"
                if outcome == "skipped"
                else "a failing test"
                if outcome == "failed"
                else "ran partway"
            )
        )
        assert _record(repo, "unit_tests", outcome, why) == 0


def test_the_rules_ship_off_and_ask_a_person(proj):
    log = EventLog(proj, "bot")
    got = C.operator_gate(log, Config(), _canon(proj, log), reason="r")
    assert not got.ok and got.ask_id and "requested" in got.message
    assert Config().approvals.policy == []


def test_an_enabled_rule_approves_its_verb_and_is_recorded_as_policy(proj):
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["skip_after_unavailable"])
    got = C.operator_gate(log, cfg, _canon(proj, log), reason="r")
    assert (
        got.ok and got.stamp["via"] == "policy" and got.stamp["policy"] == "skip_after_unavailable"
    )
    row = asks_of(_state(proj))[got.ask_id]
    assert row["policy"] is True and row["answered_by"] == "policy:skip_after_unavailable"
    assert row["human"] is False and (row["used"], row["uses"]) == (1, 1)
    assert any(
        "granted by standing rule policy:skip_after_unavailable" in f
        for f in AU.findings(_state(proj), cfg)
    )
    rows = AU.rows(_state(proj), cfg)
    assert rows[0]["approver"] == "policy:skip_after_unavailable" and rows[0]["channel"] == "policy"


def test_the_cap_and_the_condition_bound_a_rule(proj):
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["skip_after_unavailable"])
    assert C.operator_gate(log, cfg, _canon(proj, log)).ok
    again = C.operator_gate(log, cfg, _canon(proj, log))
    assert not again.ok  # one use per item and target: a person is asked
    cfg.approvals.policy_max_uses = 2
    assert C.operator_gate(log, cfg, _canon(proj, log)).ok
    cfg.approvals.policy_unavailable_runs = 4  # only three unavailable runs on record
    cfg.approvals.policy_max_uses = 9
    assert not C.operator_gate(log, cfg, _canon(proj, log)).ok


def test_a_rule_approves_nothing_but_its_own_verb_and_unknown_ids_do_nothing(proj):
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["accept_baseline_red", "no_such_rule"])
    assert not C.operator_gate(log, cfg, _canon(proj, log)).ok  # a skip is not an accept
    assert set(PO.RULES) == {
        "skip_after_unavailable",
        "accept_baseline_red",
        "complete_unavailable",
        "claim_unavailable",
    }
    assert all(
        r.verb in {"gate.skip.failed_review", "approve.partial", "complete.force", "claim.force"}
        for r in PO.RULES.values()
    )


def test_policy_is_not_a_way_to_answer_as_a_person(proj):
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["skip_after_unavailable"])
    ask = E.request(log, cfg, subject="x", digest="d")
    policy_actor = A.resolve_actor(cfg, "policy:skip_after_unavailable", requested_agent="bot")
    with pytest.raises(E.AskRefused):
        E.answer(log, cfg, ask.id, "approved", actor=policy_actor, digest="d")
    assert "policy" not in E.ANSWER_SOURCES


def test_a_policy_use_reaches_the_gate_skip_end_to_end(proj):
    cfgfile = proj / ".ddflow" / "config.toml"
    cfgfile.write_text(cfgfile.read_text() + '\n[approvals]\npolicy = ["skip_after_unavailable"]\n')
    code, out, err = run_cli(proj, "gate", "skip", "T1", "critic", "--reason", "r", agent="bot")
    assert code == 0, (out, err)
    run = [r for r in _state(proj).items["T1"].runs_of("critic") if r.outcome == "skipped"][-1]
    assert run.evidence["approval"]["via"] == "policy"
    assert run.evidence["approval"]["approved_by"] == "policy:skip_after_unavailable"


def test_a_rule_never_approves_a_verb_that_needs_more_than_level_one(proj, monkeypatch):
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["skip_after_unavailable"])
    t3 = PO.Rule("t3rule", "config.reviewer_command", "x", lambda it, target, c: True)
    monkeypatch.setitem(PO.RULES, "t3rule", t3)
    cfg.approvals.policy = ["t3rule"]
    from ddflow.services.approvals.canon import Canon

    canon = Canon(verb="config.reviewer_command", agent="bot", item="T1")
    assert not C.operator_gate(log, cfg, canon).ok


def test_a_standing_rule_is_consulted_before_the_request_hint_of_a_no_auto_site(proj, monkeypatch):
    log = EventLog(proj, "bot")
    rule = PO.Rule("always_complete", "complete.force", "x", lambda it, target, c: True)
    monkeypatch.setitem(PO.RULES, "always_complete", rule)
    cfg = _cfg(policy=["always_complete"])
    from ddflow.services.approvals.canon import Canon

    canon = Canon(verb="complete.force", agent="bot", item="T1", state="s")
    assert C.operator_gate(log, cfg, canon, auto=False).ok  # not "re-run with --approval request"
    assert not C.operator_gate(log, Config(), canon, auto=False).ok


def test_an_explicit_request_is_not_auto_answered_by_a_standing_rule(proj, monkeypatch):
    """Bef8d328af3: `--approval request` files a request and waits for a person; a standing
    rule answers refusals the agent did NOT make an explicit request of, never the request."""
    _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    _only_unavailable_gate(proj, "unavailable")
    canon = Canon(verb="complete.force", agent="bot", item="T1", target="unit_tests", state="s")
    cfg = _cfg(policy=["complete_unavailable"], policy_max_uses=99)
    # without `--approval request` the standing rule still answers (the window is open) ...
    assert C.operator_gate(log, cfg, canon).ok
    # ... but an explicit request files an ask and is never answered by the standing rule.
    got = C.operator_gate(log, cfg, canon, approval_id=C.REQUEST)
    assert not got.ok and got.ask_id
    row = asks_of(_state(proj))[got.ask_id]
    assert row["policy"] is False and row["answered_by"] == ""


def _frozen_clock(monkeypatch):
    from datetime import UTC, datetime

    t = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(clock, "now_utc", lambda: t[0])
    return t


def test_a_windowed_rule_answers_complete_force_with_its_digest_while_the_window_is_open(
    proj,
    monkeypatch,
):
    _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    _only_unavailable_gate(proj, "unavailable")
    canon = Canon(verb="complete.force", agent="bot", item="T1", target="unit_tests", state="s")
    cfg = _cfg(policy=["complete_unavailable"], policy_max_uses=99)
    got = C.operator_gate(log, cfg, canon)
    assert got.ok and got.stamp["via"] == "policy" and got.stamp["policy"] == "complete_unavailable"
    row = asks_of(_state(proj))[got.ask_id]
    assert row["policy"] is True and row["answered_by"] == "policy:complete_unavailable"
    assert row["digest"] == canon.digest()
    assert PO.window_start(_state(proj), cfg) > 0.0  # the fold persisted the opened window


def test_complete_unavailable_never_auto_answers_a_genuinely_failed_gate(proj):
    """The rule answers only `unavailable`/`partial`. A `unit_tests` that genuinely FAILED is
    never auto-approved: the operator is asked instead of a standing answer `policy:`."""
    _only_unavailable_gate(proj, "failed")
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["complete_unavailable"], policy_max_uses=99)
    got = C.operator_gate(
        log,
        cfg,
        Canon(
            verb="complete.force",
            agent="bot",
            item="T1",
            target="unit_tests",
            state="unit_tests failed",
        ),
    )
    assert not got.ok and got.ask_id  # a person is asked; a failed gate is never answered


def test_complete_unavailable_blocks_when_a_required_gate_failed(proj):
    """The OVER-FIRE guard and the required-gate check: a required gate recorded `failed`
    blocks even when `target` names only a DIFFERENT gate, and a target naming a
    NON-required gate never answers either."""
    from ddflow.core.records.gates import GateRecord
    from ddflow.core.records.items import Item

    it = Item(
        id="T1",
        kind="task",
        gates={
            "implement": GateRecord(gate="implement", outcome="passed"),
            "unit_tests": GateRecord(gate="unit_tests", outcome="failed"),
            "merge": GateRecord(gate="merge", outcome="unavailable"),
        },
    )
    cfg = _cfg(policy=["complete_unavailable"], policy_max_uses=99)
    assert PO.RULES["complete_unavailable"].when(it, "merge", cfg) is False  # unit_tests failed
    assert PO.RULES["complete_unavailable"].when(it, "lint", cfg) is False  # lint not required


def test_complete_unavailable_never_answers_failing_tests_the_base_could_not_measure():
    """An `unavailable` from the baseline probe sits over tests that DID fail on the branch: the
    tool that could not run is the comparison, not the suite. A person decides that one."""
    from types import SimpleNamespace

    from ddflow.api.lifecycle.complete.approval import _only_unavailable
    from ddflow.core.records.gates import GateRecord
    from ddflow.core.records.items import Item

    v = SimpleNamespace(unmet_gates=["unit_tests"], blockers=["b"], coverage_gaps=["unit_tests"])
    tool = GateRecord(gate="unit_tests", outcome="unavailable")
    blind = GateRecord(
        gate="unit_tests", outcome="unavailable", evidence={"baseline": {"unmeasured": ["t::a"]}}
    )
    assert _only_unavailable(v, Item(id="T1", kind="task", gates={"unit_tests": tool}))
    assert not _only_unavailable(v, Item(id="T1", kind="task", gates={"unit_tests": blind}))


def test_complete_unavailable_never_answers_a_person_only_baseline_red_partial():
    """B-complete-self-approves-over-person-only-partial: a `partial` is a coverage gap the
    standing `complete_unavailable` rule answers; but a MEASURED baseline-red partial whose
    `baseline.auto.accepted` is False (its failing tests sit in a file the item changed or
    reaches) is a person's call -- `gate_accept.next_step` names only `ddflow approve` -- and
    must not be auto-answered."""
    from types import SimpleNamespace

    from ddflow.api.lifecycle.complete.approval import _only_unavailable
    from ddflow.core.records.gates import GateRecord
    from ddflow.core.records.items import Item

    v = SimpleNamespace(unmet_gates=["unit_tests"], blockers=["b"], coverage_gaps=["unit_tests"])
    coverage = GateRecord(gate="unit_tests", outcome="partial")
    person_only = GateRecord(
        gate="unit_tests",
        outcome="partial",
        evidence={
            "baseline": {
                "red": True,
                "auto": {
                    "accepted": False,
                    "why": "failing test file(s) the item changed or reaches: tests/t.py",
                },
            }
        },
    )
    assert _only_unavailable(v, Item(id="T1", kind="task", gates={"unit_tests": coverage}))
    assert not _only_unavailable(v, Item(id="T1", kind="task", gates={"unit_tests": person_only}))


def test_complete_unavailable_predicate_and_site_share_the_person_only_guard():
    """B-complete-self-approves-over-person-only-partial: the rule's OWN predicate
    (`_unavailable_unmet_only`) refuses a person-only baseline-red partial, through the SAME
    `gate_accept.person_only` helper the completion site reads, so they cannot drift apart."""
    from ddflow.core.records.gates import GateRecord
    from ddflow.core.records.items import Item

    cfg = _cfg(policy=["complete_unavailable"], policy_max_uses=99)
    it = Item(
        id="T1",
        kind="task",
        gates={
            "unit_tests": GateRecord(
                gate="unit_tests",
                outcome="partial",
                evidence={"baseline": {"red": True, "auto": {"accepted": False}}},
            ),
            "implement": GateRecord(gate="implement", outcome="passed"),
            "merge": GateRecord(gate="merge", outcome="passed"),
        },
    )
    assert PO.RULES["complete_unavailable"].when(it, "unit_tests", cfg) is False
    # The complementary case at this same boundary: a plain unavailable coverage gap (not
    # person-only) IS answered -- the same predicate returns True there, so it distinguishes
    # the two and cannot be a blanket `lambda *a: False`.
    gap = Item(
        id="T2",
        kind="task",
        gates={
            "unit_tests": GateRecord(gate="unit_tests", outcome="unavailable"),
            "implement": GateRecord(gate="implement", outcome="passed"),
            "merge": GateRecord(gate="merge", outcome="passed"),
        },
    )
    assert PO.RULES["complete_unavailable"].when(gap, "unit_tests", cfg) is True


def _only_unavailable_repo(repo, unit):
    """A minimal task whose pipeline is exactly implement,unit_tests,merge and whose ONLY unmet
    required gate is `unit_tests` = `unit`. reviewer independence is off so the completion
    verdict has no other blocker, mirroring `tests/test_accept_partial_gate.py`."""
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write(
            "\n[agent]\nreviewer_family_must_differ = false\n"
            '[approvals]\nstanding_window_s = 86400\npolicy = ["complete_unavailable"]\n'
        )
    run_cli(repo, "workflow", "pipeline", "task", "implement,unit_tests,merge")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree", agent="bot")
    _only_unavailable_gate(repo, unit)


def test_a_routine_completion_auto_answers_only_an_unavailable_gate_end_to_end(repo):
    _only_unavailable_repo(repo, "unavailable")
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-4", agent="bot")
    assert code == 0, (out, err)
    ev = [e for e in EventLog(repo, "bot").read_all() if e.kind == "item.completed"][-1]
    assert ev.data["approval"]["policy"] == "complete_unavailable"
    assert ev.data["forced"] is True and ev.data["overridden"]
    assert ev.data["approval"]["digest"]


def test_a_routine_completion_auto_answers_a_skipped_gate_end_to_end(repo):
    """Bb67dc543b6: a `skipped` required gate (recorded with a reason -- e.g. `unit_tests`
    after a merge removed the worktree) is a coverage gap the standing `complete_unavailable`
    rule answers, exactly like `unavailable`; it must not force an operator `complete.force`."""
    _only_unavailable_repo(repo, "skipped")
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-4", agent="bot")
    assert code == 0, (out, err)
    ev = [e for e in EventLog(repo, "bot").read_all() if e.kind == "item.completed"][-1]
    assert ev.data["approval"]["policy"] == "complete_unavailable"
    assert ev.data["forced"] is True and ev.data["overridden"]


def test_the_complete_unavailable_predicate_answers_a_skipped_gate(proj):
    """The rule's own predicate (`_unavailable_unmet_only`) accepts a `skipped` required gate
    the same way the completion site does, so the two cannot drift (Bb67dc543b6)."""
    from ddflow.core.records.gates import GateRecord
    from ddflow.core.records.items import Item

    cfg = _cfg(policy=["complete_unavailable"], policy_max_uses=99)
    it = Item(
        id="T1",
        kind="task",
        gates={
            "unit_tests": GateRecord(
                gate="unit_tests", outcome="skipped", reason="merge removed it"
            ),
            "implement": GateRecord(gate="implement", outcome="passed"),
            "merge": GateRecord(gate="merge", outcome="passed"),
        },
    )
    assert PO.RULES["complete_unavailable"].when(it, "unit_tests", cfg) is True


def test_a_routine_completion_never_auto_answers_a_failed_gate_end_to_end(repo):
    _only_unavailable_repo(repo, "failed")
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-4", agent="bot")
    assert code == 3, (out, err)
    assert _state(repo).items["T1"].state != "done"


def _person_only_baseline_red(repo):
    """`_only_unavailable_repo` with unit_tests = a MEASURED baseline-red partial whose
    `baseline.auto.accepted` is False (its failing tests are the item's own): the exact
    state only a person accepts (`ddflow approve T1 unit_tests`), never the standing rule."""
    _only_unavailable_repo(repo, "partial")
    log = EventLog(repo, "ddflow-gate-run")
    log.append(
        "gate.partial",
        "T1",
        {
            "gate": "unit_tests",
            "by": "ddflow",
            "reason": "baseline-red: tests/t.py::test_x, bug B1",
            "evidence": {
                "baseline": {
                    "red": True,
                    "base": "main",
                    "ids": ["tests/t.py::test_x"],
                    "auto": {
                        "accepted": False,
                        "why": "failing test file(s) the item changed or reaches: tests/t.py",
                    },
                }
            },
        },
    )


def test_a_routine_completion_never_auto_answers_a_person_only_baseline_red_partial(repo):
    """B-complete-self-approves-over-person-only-partial: a plain `complete` (no --approval)
    over a baseline-red partial whose failing tests are the item's own must refuse and
    surface `ddflow approve`, never silently spend the standing `complete_unavailable` rule."""
    _person_only_baseline_red(repo)
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-4", agent="bot")
    assert code == 3, (out, err)
    assert "ddflow approve" in out + err
    assert _state(repo).items["T1"].state != "done"


def test_a_person_accepted_baseline_red_partial_is_not_refused_for_the_agent_complete(repo):
    """B-complete-self-approves-over-person-only-partial, round-2: once a PERSON accepts the
    measured baseline-red partial (`ddflow approve T1 unit_tests`), the verdict settles that
    gate (`gate_accept.accepted`, completion/rules `_unsettled`), so `gate_accept.person_only`
    is never consulted for it and a plain agent `complete` completes -- NOT the refusal the
    UNACCEPTED case gets. The refusal keys on the unsettled state, not on the person's record
    of it, so an operator's acceptance is honoured."""
    _person_only_baseline_red(repo)
    code, out, err = run_cli(repo, "approve", "T1", "unit_tests", "--note", "accepted the base bug")
    assert code == 0, (out, err)
    assert _state(repo).items["T1"].gate_outcome("unit_tests") == "partial", (
        "acceptance keeps the partial outcome; it does not pass it"
    )
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-4", agent="bot")
    assert code == 0, (out, err)
    assert _state(repo).items["T1"].state == "done"


def test_a_windowed_rule_stops_the_instant_the_window_lapses_and_a_person_is_asked(
    proj,
    monkeypatch,
):
    from datetime import timedelta

    frozen = _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    _only_unavailable_gate(proj, "unavailable")
    canon = Canon(verb="complete.force", agent="bot", item="T1", target="unit_tests", state="s")
    claim = Canon(verb="claim.force", agent="bot", item="T1", state="s")
    cfg = _cfg(
        policy=["complete_unavailable", "claim_unavailable"],
        policy_max_uses=99,
        standing_window_s=120,  # window below use_ttl_s (3600): lapse must be the WINDOW, not the TTL
    )
    first = C.operator_gate(log, cfg, canon)
    assert first.ok  # opens the window, anchored now
    # `claim_unavailable` answers a vanished/lapsed HOLDER (B-claim-unavailable-fires-past-deps):
    # give T1 a lapsed lease (holder gone, TTL long past) so this claim.force is the takeover the
    # rule documents -- never a no-holder item, which the rule must refuse, not auto-approve.
    log.append(
        "lease.acquired",
        "T1",
        {"holder": "gone", "at": 0.0, "ttl_s": 1, "globs": ["t.py"]},
    )
    # A SECOND generic T2 refusal while the window is still open is also auto-approved ...
    still = C.operator_gate(log, cfg, claim)
    assert (
        still.ok and still.stamp["via"] == "policy" and still.stamp["policy"] == "claim_unavailable"
    )
    frozen[0] = frozen[0] + timedelta(seconds=cfg.approvals.standing_window_s + 1)
    after = C.operator_gate(log, cfg, canon)
    assert not after.ok and after.ask_id and "requested" in after.message  # a person is asked
    assert after.ask_id != first.ask_id  # a NEW, still-unanswered ask, not the opening one


def test_a_later_standing_fire_reopens_a_window_the_opening_fire_has_lapsed(
    proj,
    monkeypatch,
):
    """B06c0d7b617: the window anchors at the LATEST in-scope standing fire, not the first. A
    second standing fire still within the window re-anchors it, so once the OPENING fire is past
    `standing_window_s` the window is open at the second fire's anchor -- the old ``min`` anchor
    would have left it permanently closed."""
    from datetime import timedelta

    frozen = _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    _only_unavailable_gate(proj, "unavailable")
    canon = Canon(verb="complete.force", agent="bot", item="T1", target="unit_tests", state="s")
    cfg = _cfg(policy=["complete_unavailable"], policy_max_uses=99, standing_window_s=120)
    first = C.operator_gate(log, cfg, canon)
    assert first.ok  # the opening fire opens the window, anchored now
    frozen[0] = frozen[0] + timedelta(seconds=60)  # still within standing_window_s
    second = C.operator_gate(log, cfg, canon)
    assert second.ok  # a later fire re-anchors the window (max anchored), never locked at the first
    # Past standing_window_s of the OPENING fire, but within it of the second: the window is
    # open at the latest fire (so this fires) and would have lapsed at the first (min would fail).
    frozen[0] = frozen[0] + timedelta(seconds=cfg.approvals.standing_window_s - 30)
    got = C.operator_gate(log, cfg, canon)
    assert got.ok and got.stamp["via"] == "policy"


def test_standing_verbs_scopes_what_the_window_covers(proj, monkeypatch):
    _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    canon = Canon(verb="complete.force", agent="bot", item="T1", state="s")
    cfg = _cfg(policy=["complete_unavailable"], policy_max_uses=99, standing_verbs=["claim.force"])
    assert not C.operator_gate(log, cfg, canon).ok  # complete.force is outside the window's scope


def test_skip_after_unavailable_is_window_gated_too(proj, monkeypatch):
    from datetime import timedelta

    frozen = _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["skip_after_unavailable"], policy_max_uses=99)
    assert C.operator_gate(log, cfg, _canon(proj, log)).ok  # opens the window
    frozen[0] = frozen[0] + timedelta(seconds=cfg.approvals.standing_window_s + 1)
    after = C.operator_gate(log, cfg, _canon(proj, log))
    assert not after.ok and after.ask_id  # skip_after_unavailable lapsed too


def _complete(proj, log, state: str, item: str = "T1"):
    return Canon(verb="complete.force", agent="bot", item=item, state=state)


def test_after_n_refusals_the_next_is_auto_approved_and_notified(proj):
    log = EventLog(proj, "bot")
    cfg = _cfg(auto_approve_after_retries=2)
    last = None
    for s in ("r1", "r2"):  # two DISTINCT digests -> two asks the operator never answered
        last = _complete(proj, log, state=s)
        got = C.operator_gate(log, cfg, last)
        assert not got.ok and got.ask_id  # filed + refused, essentially: a person was asked
    canon = _complete(proj, log, state="r3")
    got = C.operator_gate(log, cfg, canon)
    assert got.ok and got.stamp["via"] == "policy" and got.stamp["policy"] == "auto-after-retries"
    row = asks_of(_state(proj))[got.ask_id]
    assert row["policy"] is True and row["answered_by"] == "policy:auto-after-retries"
    assert row["digest"] == canon.digest()  # the request's digest is preserved
    assert row["notify"] == 2  # the notification record: the refused count that triggered it
    rows = AU.rows(_state(proj), cfg)
    assert any(r["approver"] == "policy:auto-after-retries" and r["notify"] for r in rows)
    assert any("auto-after-retries" in f and "refusal" in f for f in AU.findings(_state(proj), cfg))


def test_zero_retry_threshold_never_auto_approves(proj):
    log = EventLog(proj, "bot")
    assert Config().approvals.auto_approve_after_retries == 0  # ships OFF
    for s in ("a", "b", "c"):
        assert not C.operator_gate(log, Config(), _complete(proj, log, state=s)).ok
    assert not any(r["policy"] for r in asks_of(_state(proj)).values())


def test_an_unchanged_state_refiled_accumulates_so_the_threshold_is_reachable(proj):
    """Bf7c4b174ae: dedup returned the SAME pending ask for a re-file of an unchanged
    claim.force/complete.force, so `refusals` stayed at 1 and a live agent stuck on one state
    never reached `auto_approve_after_retries`. Each re-file of a retry verb while the
    threshold is armed is a NEW filing, so the count accumulates and the next filing fires."""
    log = EventLog(proj, "bot")
    cfg = _cfg(auto_approve_after_retries=2)
    canon = _complete(proj, log, state="digest-unchanged")  # the SAME digest every filing
    first = C.operator_gate(log, cfg, canon)
    second = C.operator_gate(log, cfg, canon)
    assert not first.ok and not second.ok
    assert first.ask_id != second.ask_id  # two DISTINCT filings, not one deduplicated ask
    got = C.operator_gate(log, cfg, canon)
    assert got.ok and got.stamp["policy"] == "auto-after-retries"
    assert asks_of(_state(proj))[got.ask_id]["notify"] == 2


def test_the_retry_rule_fires_on_count_alone_not_the_window_or_the_policy_list(proj, monkeypatch):
    from datetime import timedelta

    frozen = _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    cfg = _cfg(auto_approve_after_retries=1)
    assert cfg.approvals.policy == []  # the retry rule is NOT a `policy` list entry
    assert not C.operator_gate(log, cfg, _complete(proj, log, state="r1")).ok  # asked, not answered
    # Advance PAST the standing window: a window-based (time-based) rule would have lapsed and
    # asked a person; the retry rule still fires, so the test refutes a window-based implementation.
    frozen[0] = frozen[0] + timedelta(seconds=cfg.approvals.standing_window_s + 1)
    got = C.operator_gate(log, cfg, _complete(proj, log, state="r2"))
    assert got.ok and got.stamp["policy"] == "auto-after-retries"  # COUNT-based, not time-based


def test_refusals_counts_unanswered_but_not_denied_or_withdrawn(proj):
    log = EventLog(proj, "bot")
    cfg = Config()
    canon = _complete(proj, log, state="r1")
    got = C.operator_gate(log, cfg, canon)
    assert not got.ok and got.ask_id  # filed + refused: a pending ask
    assert PO.refusals(_state(proj), canon) == 1
    E.withdraw(log, got.ask_id, actor=A.resolve_actor(cfg, "me"))
    assert PO.refusals(_state(proj), canon) == 0  # withdrawn is not a refusal
    canon2 = _complete(proj, log, state="r2")
    got2 = C.operator_gate(log, cfg, canon2)
    E.answer(
        log, cfg, got2.ask_id, "denied", actor=A.resolve_actor(cfg, "me"), digest=canon2.digest()
    )
    assert PO.refusals(_state(proj), canon2) == 0  # a denied ask WAS answered: not a refusal


def test_a_retry_answer_does_not_open_the_standing_window(proj, monkeypatch):
    _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    cfg = _cfg(auto_approve_after_retries=2)
    for s in ("r1", "r2"):
        assert not C.operator_gate(log, cfg, _complete(proj, log, state=s)).ok
    got = C.operator_gate(log, cfg, _complete(proj, log, state="r3"))
    assert got.ok and got.stamp["policy"] == "auto-after-retries"
    # the retry answer is COUNT-based and must never anchor the standing window for window rules
    assert PO.window_start(_state(proj), cfg) == 0.0
