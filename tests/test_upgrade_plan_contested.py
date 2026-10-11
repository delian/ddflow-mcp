"""B-upgrade-plan-contested: the upgrade plan says which same-id records become contested.

Since B230e852e10 a decision, lesson or research id defined differently by two agents is
CONTESTED and `ddflow doctor` reports it (exit 1). A project whose log already holds such an
old duplicate used to learn that only AFTER upgrading: `upgrade --plan`, the MCP connect
proposal and the notice said nothing, `--apply` upgraded past it, and the upgrade dry run
failed with a bare "doctor exited 1". Now the plan lists each one, with the rival authors and
the exact `ddflow resolve` command; an apply prints the commands and resolves nothing for you;
and once the operator resolves, the plan is empty again.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import upgrade_dryrun as DR
from ddflow.services import upgrade_plan as UP
from ddflow.services import upgrade_start as US


def _rivals(repo: Path) -> None:
    """alice and bob each define decision D-x in a clone that has not seen the other; the
    merged view is ``repo`` with both shards (what a git merge does)."""
    assert run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py")[0] == 0
    clones = {}
    for agent in ("alice", "bob"):
        clone = repo.parent / agent
        shutil.copytree(repo, clone)
        title, text = ("use sqlite", "Use sqlite") if agent == "alice" else ("use pg", "Use pg")
        argv = ["decision", "add", "--id", "D-x", "--title", title, "--decision", text]
        code, out, err = run_cli(
            clone, *argv, "--globs", "src/a.py", "--by", "operator", agent=agent
        )
        assert code == 0, out + err
        clones[agent] = clone
    for agent, clone in clones.items():
        dst = repo / ".ddflow" / "events" / f"{agent}.jsonl"
        shutil.copyfile(clone / ".ddflow" / "events" / f"{agent}.jsonl", dst)


def _records(repo: Path) -> list[dict]:
    _code, out, err = run_cli(repo, "--json", "upgrade")
    assert out.strip().startswith("{"), f"no JSON body: {out!r} {err!r}"
    return json.loads(out)["categories"]["records"]


def test_the_plan_lists_a_contested_record_with_its_rivals_and_the_resolve_command(repo):
    _rivals(repo)

    found = _records(repo)

    assert [i["id"] for i in found] == ["records:contested:decision:D-x"]
    item = found[0]
    assert item["action"] == "note"  # the operator resolves it; an apply cannot
    assert "alice" in item["summary"] and "bob" in item["summary"]
    assert "ddflow resolve D-x --keep <event-id|agent>" in item["fix"]
    assert item["rivals"] and {r["agent"] for r in item["rivals"]} == {"alice", "bob"}
    code, out, _ = run_cli(repo, "upgrade")
    assert code == 1
    assert "contested records (1)" in out and "ddflow resolve D-x --keep" in out


def test_after_resolve_the_plan_has_no_contested_records(repo):
    _rivals(repo)
    assert _records(repo)

    code, out, err = run_cli(repo, "resolve", "D-x", "--keep", "bob")
    assert code == 0, out + err

    assert _records(repo) == []
    assert run_cli(repo, "doctor")[0] == 0


def test_an_apply_does_not_upgrade_past_them_silently(repo):
    _rivals(repo)

    _code, out, _err = run_cli(repo, "upgrade", "--apply")

    assert "records:contested:decision:D-x" in out
    assert "ddflow resolve D-x --keep" in out, "the apply prints the exact command"
    assert _records(repo), "an apply resolves nothing for the operator"
    assert run_cli(repo, "doctor")[0] == 1


def test_the_connect_proposal_and_the_notice_warn_about_them(repo):
    _rivals(repo)
    log = EventLog(repo)
    st = fold(log.read_all(), strict=False)
    cfg = Config()
    plan = UP.build(repo, log, cfg, st)

    proposal = US._proposal(plan, "9.9.9", "0.1.0")
    assert "D-x" in proposal and "alice" in proposal and "ddflow resolve D-x --keep" in proposal
    assert "D-x" in UP.contested_clause(plan)


def test_the_dry_run_tolerates_exactly_the_planned_contests(repo):
    _rivals(repo)
    out = run_cli(repo, "doctor")[1]

    assert DR.contested_only(out, {"decision D-x"}), out
    assert not DR.contested_only(out, set()), "an unplanned contest is still a failure"
    other = out.replace("1 problem(s)", "2 problem(s)") + "\nsomething else is wrong\n"
    assert not DR.contested_only(other, {"decision D-x"}), "a second, unplanned problem fails"
