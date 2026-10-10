"""B-upgrade.11-orchestrate-monitor: `ddflow upgrade --status | --history | --resume`.

D-self-upgrade (6). A throwaway project, no network: the plan is built by hand (three
opt-in-feature steps, which an apply only acknowledges) and the "interruption" is a
KeyboardInterrupt raised from the applier of one step.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import run_cli

from ddflow import api as A
from ddflow.api._base import _load
from ddflow.services import upgrade_apply as UA
from ddflow.services import upgrade_status as UST
from ddflow.surfaces.mcp import Server

RUNNING = "9.9.9"


@pytest.fixture
def project(repo: Path) -> Path:
    (repo / ".ddflow").mkdir()
    return repo


def plan_of(*ids: str, running: str = RUNNING) -> dict[str, Any]:
    items = [
        {
            "id": i,
            "category": "features",
            "action": "note",
            "summary": f"feature {i}",
            "fix": "",
        }
        for i in ids
    ]
    return {
        "running": running,
        "project_version": "9.0.0",
        "up_to_date": not items,
        "total": len(items),
        "categories": {"features": items},
    }


def apply(project: Path, plan: dict[str, Any], **kw: Any) -> dict[str, Any]:
    log, cfg, st = _load(project, "orch")
    return UA.apply(
        project, log, cfg, st, categories="features", backup="none", agent="orch", plan=plan, **kw
    )


def state(project: Path):
    return _load(project, "orch")


def interrupt_at(monkeypatch: pytest.MonkeyPatch, step: str | None) -> list[str]:
    """Make the applier stop the whole run at ``step`` (None: let everything through)."""
    ran: list[str] = []
    real = UA._run

    def fake(repo, cfg, item, agent):
        if item["id"] == step:
            raise KeyboardInterrupt
        ran.append(item["id"])
        return real(repo, cfg, item, agent)

    monkeypatch.setattr(UA, "_run", fake)
    return ran


def by_id(data: dict[str, Any]) -> dict[str, str]:
    return {s["id"]: s["state"] for s in data["steps"]}


def test_steps_show_their_state_through_an_interrupted_run(project, monkeypatch) -> None:
    plan = plan_of("f1", "f2", "f3")
    interrupt_at(monkeypatch, "f2")
    with pytest.raises(KeyboardInterrupt):
        apply(project, plan)
    _log, cfg, st = state(project)
    # just now: the run looks alive; f1 is done, the one it was on is running, f3 waits
    live = UST.status(project, cfg, st, plan=plan, running=RUNNING)
    assert live["run"]["state"] == "running"
    assert by_id(live) == {"f1": "done", "f2": "running", "f3": "pending"}
    # an hour on, nobody is working on it
    later = UST.status(project, cfg, st, plan=plan, running=RUNNING, now=time.time() + 3600)
    assert later["run"]["state"] == "interrupted"
    assert by_id(later) == {"f1": "done", "f2": "pending", "f3": "pending"}
    assert "ddflow upgrade --resume" in later["text"]


def test_resume_finishes_without_repeating_done_steps(project, monkeypatch) -> None:
    plan = plan_of("f1", "f2", "f3")
    interrupt_at(monkeypatch, "f2")
    with pytest.raises(KeyboardInterrupt):
        apply(project, plan)
    monkeypatch.undo()
    ran = interrupt_at(monkeypatch, None)
    _log, _cfg, st = state(project)
    rid, run = UST.open_run(st)
    assert rid and run["categories"] == ["features"]
    done = apply(project, plan, watch=UA.Watch(resume=rid))
    assert ran == ["f2", "f3"], "f1 was settled by the interrupted run and must not run again"
    assert done["exit"] == 0 and done["to"] == RUNNING
    _log, cfg, st = state(project)
    assert UST.open_run(st) == ("", {})
    assert [u["items"] for u in st.upgrades] == [["f1", "f2", "f3"]]
    assert st.upgrades[0]["run"] == rid
    final = UST.status(project, cfg, st, plan=plan_of(), running=RUNNING)
    assert final["run"] == {} and final["up_to_date"]


def test_a_failed_run_closes_and_is_not_an_upgrade(project, monkeypatch) -> None:
    def broken(repo, cfg, item, agent):
        return UA.FAILED, "boom"

    monkeypatch.setattr(UA, "_run", broken)
    out = apply(project, plan_of("f1"))
    assert out["exit"] == 1
    _log, _cfg, st = state(project)
    assert st.upgrades == [] and UST.open_run(st) == ("", {})
    (run,) = st.upgrade_runs.values()
    assert run["closed"] == "failed" and run["steps"][0]["status"] == UA.FAILED


def test_status_names_an_agent_still_on_the_old_version(project) -> None:
    _log, cfg, st = state(project)
    st.ddflow_versions = {
        "9.0.0": {"agents": ["laggard", "keeper"], "at": "2026-01-01T00:00:00Z"},
        RUNNING: {"agents": ["keeper"], "at": "2026-02-01T00:00:00Z"},
    }
    data = UST.status(project, cfg, st, plan=plan_of(), running=RUNNING)
    assert [b["agent"] for b in data["behind"]] == ["laggard"]
    assert "laggard: 9.0.0" in data["text"]


def test_history_lists_two_upgrades(project) -> None:
    apply(project, plan_of("a"))
    apply(project, plan_of("b", running="10.0.0"))
    _log, _cfg, st = state(project)
    rows = UST.history(st)
    assert [r["to"] for r in rows] == [RUNNING, "10.0.0"]
    text = UST.render_history(rows)
    assert "2 upgrade(s)" in text and "-> 10.0.0" in text


def test_nothing_to_resume_is_exit_2(project) -> None:
    out = A.upgrade(project, resume=True)
    assert out.exit == 2 and "nothing to resume" in out.reason


def test_the_modes_stand_alone(project) -> None:
    assert A.upgrade(project, status=True, apply="all").exit == 3
    assert A.upgrade(project, history=True, check=True).exit == 3
    assert A.upgrade(project, resume=True, apply="all").exit == 3


def test_status_reports_the_cached_release_and_the_backups(project) -> None:
    from ddflow.services import upgrade_check as UC

    UC._update_cache(project, lambda cur: {**cur, "newest": "99.0.0", "checked_at": 1700000000.0})
    (project / ".ddflow" / "backups" / "20260101T000000-a-to-b").mkdir(parents=True)
    (project / ".ddflow" / "backups" / "20260101T000000-a-to-b" / "manifest.json").write_text("{}")
    _log, cfg, st = state(project)
    data = UST.status(project, cfg, st, plan=plan_of(), running=RUNNING)
    assert data["newest"] == "99.0.0" and data["newer"]
    assert "2023-11-14" in data["text"]
    assert data["backups"] == ["20260101T000000-a-to-b"]


def test_cli_and_mcp_mirror_each_other(project) -> None:
    code, out, _err = run_cli(project, "upgrade", "--history", "--json")
    assert code == 0
    cli = json.loads(out)
    reply = Server(project).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_upgrade", "arguments": {"history": True}},
        }
    )
    mcp = json.loads(reply["result"]["content"][0]["text"])
    assert cli == mcp and cli["upgrades"] == []
    code, out, _err = run_cli(project, "upgrade", "--status", "--json")
    cli = json.loads(out)
    reply = Server(project).handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ddflow_upgrade", "arguments": {"status": True}},
        }
    )
    mcp = json.loads(reply["result"]["content"][0]["text"])
    assert set(cli) == set(mcp) and cli["running"] == mcp["running"]
    assert code in (0, 1)


def test_mcp_reports_each_step_as_progress(project, monkeypatch) -> None:
    from ddflow.services import upgrade_plan as UP

    monkeypatch.setattr(UP, "build", lambda *a, **k: plan_of("f1", "f2"))
    sent: list[dict] = []
    server = Server(project)
    server.notify = sent.append
    reply = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_upgrade",
                "arguments": {"apply": "features", "backup": "none"},
                "_meta": {"progressToken": "tok"},
            },
        }
    )
    assert reply["result"].get("isError") is not True
    steps = [f["params"]["message"] for f in sent if f["method"] == "notifications/progress"]
    assert steps == ["[acknowledged] f1", "[acknowledged] f2"]


def test_help_topic_and_readme_section_exist() -> None:
    root = Path(__file__).parent.parent
    readme = (root / "README.md").read_text()
    assert "### Upgrading ddflow" in readme
    for mode in ("--check", "--status", "--history", "--resume", "--restore", "--self"):
        assert mode in readme
    topic = (root / "ddflow/templates/prompts/help/upgrade.md").read_text()
    assert "--resume" in topic and "never install" in topic
