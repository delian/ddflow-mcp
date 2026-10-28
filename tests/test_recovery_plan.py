"""`core/recovery_plan.py`: the pure triage behind `ddflow recover --all --plan`
(DX-recovery-plan, orphan-recovery.md research section 3).

One engine over the measured facts — the lease sweep's rows, orphaned gate-run markers,
unended jobs, and merge-queue entries left running by a dead integrator. It never acts:
`services.recovery` applies SAFE_AUTO, records, and shows the rest. These tests pin the
classification, the attempt cap, the exact command on every action, and that a plan is
always shown before anything runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli

from ddflow.core import recovery_plan as RP
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _row(
    item="T1",
    kind="expired_lease",
    salvageable=None,
    advice="",
    holder="a",
    age_s=10.0,
    adopted=False,
    unmerged=0,
):
    return {
        "item": item,
        "kind": kind,
        "worktree": f"/wt/{item}",
        "advice": advice,
        "holder": holder,
        "salvageable": salvageable,
        "adopted": adopted,
        "age_s": age_s,
        "unmerged_commits": unmerged,
    }


def _facts(**kw) -> RP.Facts:
    return RP.Facts(
        recoveries=kw.get("recoveries", []),
        dead=kw.get("dead", []),
        jobs=kw.get("jobs", []),
        queue=kw.get("queue", []),
        attempts=kw.get("attempts", {}),
        kill_switch=kw.get("kill_switch", False),
    )


def _one(facts: RP.Facts, **kw) -> list[RP.Action]:
    return RP.triage(facts, now=1000.0, limits=RP.Limits(max_attempts=2), **kw)


class TestExpiredLease:
    def test_clean_tree_is_safe_auto_expire(self):
        """A measured-clean expired lease loses nothing to expiry: SAFE_AUTO frees the claim."""
        (a,) = _one(_facts(recoveries=[_row(salvageable=False)]))
        assert a.cls == RP.SAFE_AUTO
        assert a.item == "T1"
        assert a.evidence["effect"] == "expire"
        assert a.command.startswith("record `lease.expired` for a's lease")
        assert a.attempts == 0

    def test_clean_tree_at_the_cap_is_operator_too(self):
        """The cap gates every recovery arm: a clean lease at the cap is OPERATOR, not a
        third automatic expiry -- the expire-to-claim loop has run its course."""
        facts = _facts(recoveries=[_row(salvageable=False)], attempts={"T1": 2})
        (a,) = _one(facts)
        assert a.cls == RP.OPERATOR
        assert a.attempts == 2
        assert "ddflow block T1" in a.command

    def test_actions_emitted_in_the_same_pass_count_toward_the_cap(self):
        """Two rows for one item one under the cap: the second must not advertise a third
        in-pass expiry (rubber-duck #7)."""
        facts = _facts(
            recoveries=[_row(salvageable=False), _row(salvageable=False)],
            attempts={"T1": 1},
        )
        out = _one(facts)
        assert [(a.cls, a.attempts) for a in out] == [(RP.SAFE_AUTO, 1), (RP.OPERATOR, 2)]

    def test_dirty_tree_is_propose_resume_with_the_exact_claim(self):
        (a,) = _one(_facts(recoveries=[_row(salvageable=True, unmerged=3)]))
        assert a.cls == RP.PROPOSE
        assert "ddflow claim T1 --force" in a.command
        assert a.attempts == 0

    def test_unmeasurable_tree_is_never_safe_auto(self):
        """`could not measure` is not `clean`: nothing automatic, ever."""
        (a,) = _one(_facts(recoveries=[_row(salvageable=None)]))
        assert a.cls == RP.PROPOSE
        assert a.evidence["effect"] == "resume"

    def test_attempt_cap_turns_resume_operator(self):
        facts = _facts(recoveries=[_row(salvageable=True)], attempts={"T1": 2})
        (a,) = _one(facts)
        assert a.cls == RP.OPERATOR
        assert a.attempts == 2
        assert "ddflow block T1" in a.command
        assert "<operator> settle" in a.reason or "cap" in a.reason

    def test_attempts_under_cap_stay_propose(self):
        facts = _facts(recoveries=[_row(salvageable=True)], attempts={"T1": 1})
        (a,) = _one(facts)
        assert a.cls == RP.PROPOSE
        assert a.attempts == 1


class TestOrphans:
    def test_clean_orphan_worktree_is_shown_not_done(self):
        (a,) = _one(_facts(recoveries=[_row(kind="orphan_worktree", salvageable=False)]))
        assert a.cls == RP.PROPOSE
        assert "ddflow cleanup --apply" in a.command

    def test_orphan_with_work_is_a_resume(self):
        (a,) = _one(_facts(recoveries=[_row(kind="orphan_worktree", salvageable=True)]))
        assert a.cls == RP.PROPOSE
        assert "ddflow claim T1" in a.command

    def test_stale_running_holding_work_is_a_resume(self):
        (a,) = _one(_facts(recoveries=[_row(kind="stale_running", salvageable=True)]))
        assert a.cls == RP.PROPOSE
        assert a.evidence["effect"] == "resume"


class TestDeadRunsJobsQueue:
    def test_dead_gate_run_is_recorded_unavailable(self):
        facts = _facts(
            dead=[{"item": "T1", "gate": "unit_tests", "pid": 99, "at": "x", "sha": "s"}]
        )
        (a,) = _one(facts)
        assert a.cls == RP.SAFE_AUTO
        assert a.evidence["effect"] == "record_unavailable"
        assert "unavailable" in a.command and "unit_tests" in a.command

    def test_a_job_whose_log_has_the_exit_is_ended(self):
        facts = _facts(jobs=[{"id": "J1", "item": "T1", "state": "exited", "exit_code": 0}])
        (a,) = _one(facts)
        assert a.cls == RP.SAFE_AUTO
        assert a.evidence["effect"] == "job_end"
        assert a.command == "ddflow job end J1"

    def test_a_gone_job_is_ended_with_its_killed_note(self):
        facts = _facts(jobs=[{"id": "J2", "item": "T1", "state": "gone", "exit_code": None}])
        (a,) = _one(facts)
        assert a.cls == RP.SAFE_AUTO
        assert a.command == "ddflow job end J2"
        assert "killed" in a.reason.lower()

    def test_running_and_elsewhere_jobs_are_left_alone(self):
        facts = _facts(
            jobs=[
                {"id": "J3", "item": "T1", "state": "running", "exit_code": None},
                {"id": "J4", "item": "T1", "state": "elsewhere", "exit_code": None},
            ]
        )
        assert _one(facts) == []

    def test_a_queue_entry_left_running_is_requeued(self):
        out = _one(_facts(queue=["T1", "T2"]))
        assert [(a.cls, a.item) for a in out] == [(RP.SAFE_AUTO, "T1"), (RP.SAFE_AUTO, "T2")]
        assert all(a.evidence["effect"] == "requeue" for a in out)
        assert out[0].evidence["items"] == ["T1"]


class TestShape:
    def test_actions_come_safe_auto_first_then_propose_then_operator(self):
        facts = _facts(
            recoveries=[_row(item="OP1", salvageable=True), _row(item="SA1", salvageable=False)],
            attempts={"OP1": 9},
        )
        out = _one(facts)
        assert [a.item for a in out] == ["SA1", "OP1"]
        assert [a.cls for a in out] == [RP.SAFE_AUTO, RP.OPERATOR]

    def test_every_action_serialises_for_the_plan_json(self):
        facts = _facts(recoveries=[_row(salvageable=False)])
        (a,) = _one(facts)
        d = a.as_dict()
        assert set(d) >= {"item", "cls", "command", "reason", "attempts", "evidence"}

    def test_nothing_in_means_no_actions_even_with_a_kill_switch(self):
        assert _one(_facts(kill_switch=True)) == []

    def test_a_kill_switch_does_not_silence_the_dry_run(self):
        """The triage itself is always a dry run: with the switch on it still prints the
        actions; refusing to run them is `services.recovery`'s contract, not this one."""
        (a,) = _one(_facts(kill_switch=True, recoveries=[_row(salvageable=False)]))
        assert a.cls == RP.SAFE_AUTO


# -- `ddflow recover --all --plan [--json]` end to end -----------------------------------


def _crashed_leaves_work(repo: Path, *, work: bool) -> str:
    """T1 claimed by a 1 s-lease agent that wrote work (or nothing) and died."""
    run_cli(repo, "init")
    import re

    cfg = repo / ".ddflow" / "config.toml"
    text = re.sub(r"(?m)^(ttl_s|grace_s) = .*\n", "", cfg.read_text())
    cfg.write_text(text.replace("[lease]\n", "[lease]\nttl_s = 1\ngrace_s = 0\n", 1))
    from ddflow.api import lifecycle as LC
    from ddflow.infra.log import EventLog

    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": ["src/*"]})
    out = LC.claim(repo, "T1", agent="crashed")
    assert out.exit == 0, out.reason
    if work:
        (Path(out.data["worktree"]) / "work.py").write_text("x = 1\n")
    import time

    time.sleep(2.2)
    return out.data["worktree"]


def test_plan_flags_the_work_and_apply_never_touches_it(repo):
    """Plan: PROPOSE with the exact claim. Apply: PROPOSE is shown, never done."""
    from ddflow.api import reporting as RP

    _crashed_leaves_work(repo, work=True)
    code, out, _ = run_cli(repo, "recover", "--all", "--plan", "--json")
    assert code == 0
    rows = json.loads(out)
    assert any(r["cls"] == "propose" and "ddflow claim T1" in r["command"] for r in rows)
    rec = RP.recover(repo, all=True, apply=True, agent="op")
    assert rec.exit == 0, rec.reason
    # The proposed claim must be REPORTED and NOT performed. The row has to be there:
    # a silent empty report is not a report, and an executed proposal would show applied.
    assert any(
        r["item"] == "T1" and r["applied"] is False and "shown only" in r["note"]
        for r in rec.data["applied_rows"]
    )
    lease = fold(EventLog(repo, "r").read_all()).items["T1"].lease
    assert lease is not None and not lease.expired_at


def test_apply_records_a_clean_expiry_and_the_kill_switch_stops_apply_only(repo):
    from ddflow.api import reporting as RP

    _crashed_leaves_work(repo, work=False)
    (repo / ".ddflow" / "local").mkdir(parents=True, exist_ok=True)
    stop = repo / ".ddflow" / "local" / "recovery.stop"
    stop.write_text("operator pulled the emergency brake\n")
    refused = RP.recover(repo, all=True, apply=True, agent="op")
    assert refused.exit == 3
    # The switch did more than refuse: the SAFE_AUTO expiry itself did NOT land.
    lease = fold(EventLog(repo, "r").read_all()).items["T1"].lease
    assert lease is not None and not lease.expired_at
    # The plan is a dry run: the switch does not silence it.
    code, out, _ = run_cli(repo, "recover", "--all", "--plan", "--json")
    assert code == 0 and out
    stop.unlink()
    rec = RP.recover(repo, all=True, apply=True, agent="op")
    assert rec.exit == 0, rec.reason
    lease = fold(EventLog(repo, "r").read_all()).items["T1"].lease
    assert lease is not None and lease.expired_at


def test_a_job_whose_log_has_the_exit_is_ended_by_apply(repo):
    from ddflow.api import reporting as RP

    run_cli(repo, "init")
    from ddflow.core.model import fold as fold_all
    from ddflow.infra.log import EventLog

    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": ["src/*"]})
    trail = repo / "job.log"
    trail.write_text("done\nddflow-job-exit: 0\n")
    log.append(
        "job.started", "J1", {"item": "T1", "command": "x", "pid": 999999, "log": str(trail)}
    )
    _code, out, _ = run_cli(repo, "recover", "--all", "--plan", "--json")
    rows = json.loads(out)
    assert any(r["cls"] == "safe_auto" and r["command"] == "ddflow job end J1" for r in rows)
    rec = RP.recover(repo, all=True, apply=True, agent="op")
    assert rec.exit == 0, rec.reason
    job = fold_all(EventLog(repo, "r").read_all()).jobs["J1"]
    assert job.ended_at and job.exit_code == 0


def test_plain_recover_is_unchanged_on_a_clean_project(repo):
    code, out, _ = run_cli(repo, "recover")
    assert code == 2
    code, out, _ = run_cli(repo, "recover", "--all", "--plan")
    assert code == 2 and not out
