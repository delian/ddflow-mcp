"""Environment branches: GitLab flow's promotion, one step downstream (RESEARCH R18).

Real repositories; "is it deployed there" is checked with `git show <env>:<file>`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import pass_pipeline, run_cli

from ddflow.config import Config
from ddflow.core import flow as F
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

AUTHOR = "claude-opus-5"
ENVS = ["pre-production", "production"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _state(repo: Path):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def _cfg(**flow) -> Config:
    c = Config()
    for k, v in flow.items():
        setattr(c.flow, k, v)
    return c


# -- pure rules ----------------------------------------------------------------------


def test_promotion_is_always_one_step_downstream():
    cfg = _cfg(environments=ENVS)
    assert F.env_chain(cfg, "main") == ["main", *ENVS]
    assert F.promotion_step(cfg, "main", "pre-production") == ("main", "pre-production")
    assert F.promotion_step(cfg, "main", "production") == ("pre-production", "production")
    with pytest.raises(ValueError, match="not an environment"):
        F.promotion_step(cfg, "main", "staging")
    gitflow = _cfg(environments=ENVS, model="gitflow")
    assert F.env_chain(gitflow, "main")[0] == "main", "gitflow promotes what was RELEASED"


def test_environment_configuration_problems_are_named():
    bad = F.problems(
        _cfg(
            environments=["qa", "qa", "maint/1.x"], lines={"1": "maint/1.x"}, auto_promote=["prod"]
        )
    )
    assert len(bad) == 3, bad


# -- end to end ----------------------------------------------------------------------


@pytest.fixture
def envs(repo):
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[flow]\nenvironments = ["pre-production", "production"]\n')
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "adopt")
    for b in ENVS:  # AFTER the adopt commit: every environment starts level with main
        _git(repo, "branch", b)
    return repo


def _land(repo: Path, item: str, *, edit: tuple[str, str] | None = None) -> dict:
    code, out, err = run_cli(repo, "--json", "claim", item)
    assert code == 0, err
    body = json.loads(out)
    tree = Path(body["worktree"])
    if edit:
        (tree / edit[0]).write_text(edit[1])
        _git(tree, "add", edit[0])
        _git(tree, "commit", "-qm", f"feat: {item}")
    pass_pipeline(repo, item)
    code, _out, err = run_cli(repo, "merge", item)
    assert code == 0, err
    code, _out, err = run_cli(repo, "complete", item, "--model", AUTHOR)
    assert code == 0, err
    return body


def test_work_reaches_production_only_through_pre_production(envs):
    repo = envs
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    _land(repo, "T1", edit=("a.py", "a\n"))

    code, _out, _err = run_cli(repo, "promote", "add", "production")
    assert code == 2, "production's upstream is pre-production, which has nothing new"

    code, out, err = run_cli(repo, "--json", "promote", "add", "pre-production")
    assert code == 0, err
    pid = json.loads(out)["id"]
    assert pid == "promote-pre-production-1" and json.loads(out)["ahead"] >= 1

    code, out, err = run_cli(repo, "--json", "gate", "status", pid)
    assert json.loads(out)["pipeline"] == ["unit_tests", "merge"], "the promotion pipeline"
    body = _land(repo, pid)
    assert body["base"] == "pre-production" and body["port"]["status"] == "clean"
    assert _git(repo, "show", "pre-production:a.py") == "a"
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", "production:a.py"], capture_output=True
        ).returncode
        != 0
    ), "production must not have it yet"
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main", "no checkout switched"

    code, out, err = run_cli(repo, "--json", "promote", "add", "production")
    assert code == 0, err
    _land(repo, json.loads(out)["id"])
    assert _git(repo, "show", "production:a.py") == "a"

    code, out, _ = run_cli(repo, "--json", "promote", "status")
    rows = {r["env"]: r for r in json.loads(out)["rows"]}
    assert rows["pre-production"]["behind"] == 0 and rows["production"]["behind"] == 0


def test_one_open_promotion_per_environment(envs):
    repo = envs
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    _land(repo, "T1", edit=("a.py", "a\n"))
    assert run_cli(repo, "promote", "add", "pre-production")[0] == 0
    code, _out, err = run_cli(repo, "promote", "add", "pre-production")
    assert code == 3 and "already open" in err


def test_an_unknown_or_missing_environment_is_refused(envs):
    repo = envs
    code, _out, err = run_cli(repo, "promote", "add", "staging")
    assert code == 3 and "not an environment" in err
    _git(repo, "branch", "-D", "production")
    code, _out, err = run_cli(repo, "promote", "add", "production")
    assert code == 3 and "does not exist" in err


def test_auto_promote_files_the_promotion_when_upstream_moves(envs):
    repo = envs
    cfgp = repo / ".ddflow" / "config.toml"
    cfgp.write_text(cfgp.read_text() + 'auto_promote = ["pre-production"]\n')
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    _code, out, _ = run_cli(repo, "--json", "next")
    assert json.loads(out)["promoted"] == [], "nothing upstream yet"
    _land(repo, "T1", edit=("a.py", "a\n"))
    _code, out, err = run_cli(repo, "--json", "next")
    body = json.loads(out)
    assert body["promoted"] == ["promote-pre-production-1"], err
    assert "promote-pre-production-1" in [i["id"] for i in body["ready"]]
    _code, out, _ = run_cli(repo, "--json", "next")
    assert json.loads(out)["promoted"] == [], "one open promotion, not one per call"
    _land(repo, "promote-pre-production-1")
    _code, out, _ = run_cli(repo, "--json", "promote", "status")
    assert {r["env"]: r["behind"] for r in json.loads(out)["rows"]}["production"] > 0
    _code, out, _ = run_cli(repo, "--json", "next")
    assert json.loads(out)["promoted"] == []
    assert not any(i.promote_to == "production" for i in _state(repo).items.values()), (
        "production is behind but NOT in auto_promote: a deploy there is a person's call"
    )


def test_a_conflicting_promotion_is_work_for_the_agent(envs):
    repo = envs
    tmp = repo.parent / "pp"
    _git(repo, "worktree", "add", "-q", str(tmp), "pre-production")
    (tmp / "a.py").write_text("hotfixed on the environment\n")
    _git(tmp, "add", "a.py")
    _git(tmp, "commit", "-qm", "direct edit")
    _git(repo, "worktree", "remove", str(tmp))
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    _land(repo, "T1", edit=("a.py", "a\n"))
    _code, out, _ = run_cli(repo, "--json", "promote", "add", "pre-production")
    code, out, err = run_cli(repo, "--json", "claim", json.loads(out)["id"])
    assert code == 0, err
    body = json.loads(out)
    assert body["port"]["status"] == "conflict" and body["port"]["files"] == ["a.py"]


def test_promotions_are_not_release_notes(envs):
    """An invariant, not a filter: a promotion's merge lives on the environment branch,
    which never flows back into the branch versions are cut from."""
    repo = envs
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    _land(repo, "T1", edit=("a.py", "a\n"))
    _code, out, _ = run_cli(repo, "--json", "promote", "add", "pre-production")
    _land(repo, json.loads(out)["id"])
    _code, out, _ = run_cli(repo, "--json", "version", "show")
    assert not any(i.startswith("promote-") for i in json.loads(out)["items"])


# -- what is live: the deploy hook records the deployed sha (B183) ------------------------


def test_a_deploy_hook_records_the_sha_and_status_says_what_is_live(envs):
    repo = envs
    old = _git(repo, "rev-parse", "production")
    code, out, err = run_cli(repo, "--json", "promote", "deployed", "production", "--sha", old)
    assert code == 0, err
    assert json.loads(out)["sha"] == old

    code, out, _ = run_cli(repo, "--json", "promote", "status")
    rows = {r["env"]: r for r in json.loads(out)["rows"]}
    assert rows["production"]["deployed"] == old[:12]
    assert rows["production"]["undeployed"] == 0 and rows["production"]["deployed_at"]
    assert rows["pre-production"]["deployed"] == "", "no hook has reported for it"

    # production's branch moves on (a promotion lands); what is LIVE has not.
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    _land(repo, "T1", edit=("a.py", "a\n"))
    for env in ENVS:
        code, out, err = run_cli(repo, "--json", "promote", "add", env)
        assert code == 0, err
        _land(repo, json.loads(out)["id"])
    code, out, _ = run_cli(repo, "--json", "promote", "status")
    prod = {r["env"]: r for r in json.loads(out)["rows"]}["production"]
    assert prod["deployed"] == old[:12] and prod["undeployed"] >= 1, prod

    code, out, err = run_cli(repo, "--json", "promote", "deployed", "production")
    assert code == 0, err
    assert json.loads(out)["sha"] == _git(repo, "rev-parse", "production"), "default: the head"
    code, out, _ = run_cli(repo, "--json", "promote", "status")
    prod = {r["env"]: r for r in json.loads(out)["rows"]}["production"]
    assert prod["undeployed"] == 0


def test_deployed_refuses_an_unknown_environment_and_a_sha_that_is_not_a_commit(envs):
    repo = envs
    code, _, err = run_cli(repo, "promote", "deployed", "staging")
    assert code == 3 and "not an environment" in err, err
    code, _, err = run_cli(repo, "promote", "deployed", "production", "--sha", "deadbeef" * 5)
    assert code == 3 and "not a commit" in err, err


def test_the_mcp_twin_records_the_same_deploy(envs):
    from ddflow.surfaces.mcp import Server

    repo = envs
    head = _git(repo, "rev-parse", "production")
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_promote_deployed", "arguments": {"env": "production"}},
        }
    )
    assert not reply["result"].get("isError"), reply
    _, out, _ = run_cli(repo, "--json", "promote", "status")
    prod = {r["env"]: r for r in json.loads(out)["rows"]}["production"]
    assert prod["deployed"] == head[:12]


def test_a_refused_deploy_record_leaves_what_is_live_untouched(envs):
    repo = envs
    good = _git(repo, "rev-parse", "production")
    assert run_cli(repo, "promote", "deployed", "production", "--sha", good)[0] == 0
    assert run_cli(repo, "promote", "deployed", "production", "--sha", "nothere" * 6)[0] == 3
    assert run_cli(repo, "promote", "deployed", "staging")[0] == 3
    code, out, _ = run_cli(repo, "--json", "promote", "status")
    prod = {r["env"]: r for r in json.loads(out)["rows"]}["production"]
    assert prod["deployed"] == good[:12], "a refusal must not overwrite the recorded sha"


def test_status_says_so_when_the_undeployed_count_cannot_be_computed(envs):
    """-1 is 'could not tell', not 'up to date': the text must not render them alike."""
    from ddflow.infra.log import EventLog as _Log

    repo = envs
    _Log(repo, "hook").append(
        "deploy.recorded", "production", {"env": "production", "sha": "f" * 40}
    )
    code, out, _ = run_cli(repo, "promote", "status")
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("production"))
    assert "live ffffffffffff" in line and "unknown" in line, line
