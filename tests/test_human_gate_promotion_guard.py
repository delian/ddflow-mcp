"""Bug Be14f271da8: the human-gate guard ignored `gates.promotion_pipeline`.

`_guarded_human_gates` collected pipeline membership from `task_pipeline` and
`phase_pipeline` only, so a tool write that dropped a human gate from the promotion
pipeline -- where the knob's own doc tells operators to put a deploy sign-off -- passed
the guard. `ddflow_configure` could silently remove the operator's approval step.
"""

from __future__ import annotations

from conftest import run_cli

from ddflow.config import Config

OK = 0


def _repo_with_promotion_signoff(repo) -> None:
    run_cli(repo, "init")
    run_cli(repo, "config", "--append-toml", '[gate.deploy_signoff]\nhuman = true\nprompt = "p"')
    code, _out, err = run_cli(
        repo,
        "config",
        "--set",
        "gates.promotion_pipeline",
        '["unit_tests", "deploy_signoff", "merge"]',
    )
    assert code == OK, err


def _promotion_pipeline(repo) -> list[str]:
    return Config.load(repo).gates.promotion_pipeline


def _configure(repo, **args) -> str:
    from ddflow.surfaces.mcp import Server

    r = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_configure", "arguments": args},
        }
    )
    return r["result"]["content"][0]["text"]


def test_a_tool_write_dropping_a_human_gate_from_promotion_pipeline_is_refused(repo):
    _repo_with_promotion_signoff(repo)
    out = _configure(repo, set="gates.promotion_pipeline", value='["unit_tests", "merge"]')
    assert "human-approval gate" in out, f"the deploy sign-off was dropped: {out}"
    assert "deploy_signoff" in _promotion_pipeline(repo)


def test_the_cli_write_dropping_it_is_refused_the_same_way(repo):
    _repo_with_promotion_signoff(repo)
    code, out, err = run_cli(
        repo, "config", "--set", "gates.promotion_pipeline", '["unit_tests", "merge"]'
    )
    assert code != OK, f"the deploy sign-off was dropped: {out}"
    assert "human-approval gate" in (out + err)
    assert "deploy_signoff" in _promotion_pipeline(repo)


def test_a_promotion_edit_that_keeps_the_human_gate_still_works(repo):
    _repo_with_promotion_signoff(repo)
    out = _configure(
        repo, set="gates.promotion_pipeline", value='["deploy_signoff", "unit_tests", "merge"]'
    )
    assert "human-approval gate" not in out, out
    assert _promotion_pipeline(repo) == ["deploy_signoff", "unit_tests", "merge"]


def test_task_pipeline_guard_is_unchanged(repo):
    run_cli(repo, "init")
    run_cli(repo, "config", "--append-toml", '[gate.plan_approved]\nhuman = true\nprompt = "p"')
    run_cli(repo, "workflow", "pipeline", "task", "plan_approved,implement,merge")
    out = _configure(repo, set="gates.task_pipeline", value='["implement", "merge"]')
    assert "human-approval gate" in out, out
