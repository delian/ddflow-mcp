"""`implement` — the unattended loop, and the `/implement` file adopt writes for Claude Code.

Ported from a project whose hand-rolled `/implement` ran a phase loop under `/loop`. What
makes it worth shipping is the part a per-item driver does not carry: WHEN the loop may
stop. Every test here pins one statement an unattended run depends on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_mcp import proj, rpc  # noqa: F401 -- `proj` is a fixture

from ddflow.services import prompts as P
from ddflow.services.adopt import AGENT_COMMANDS, MANAGED_MARK

CMD = ".claude/commands/implement.md"


def _get(repo, arguments):
    r = rpc(
        repo,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "prompts/get",
                "params": {"name": "implement", "arguments": arguments},
            }
        ],
    )
    assert "error" not in r[0], r[0].get("error")
    return r[0]["result"]["messages"][0]["content"]["text"]


def test_implement_is_advertised_as_an_mcp_prompt_with_a_scope(proj):  # noqa: F811
    r = rpc(proj, [{"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}])
    by_name = {p["name"]: p for p in r[0]["result"]["prompts"]}
    assert "implement" in by_name
    assert [a["name"] for a in by_name["implement"].get("arguments", [])] == ["scope"]


def test_a_scope_is_rendered_into_the_loop_and_its_absence_means_the_whole_queue(proj):  # noqa: F811
    scoped = _get(proj, {"scope": "B17"})
    assert "`B17`" in scoped and "No scope was named" not in scoped
    unscoped = _get(proj, {})
    assert "No scope was named" in unscoped and "Do not ask which" in unscoped


@pytest.mark.parametrize(
    "must_say",
    [
        "exactly FOUR cases",  # the stop contract -- the reason this command exists
        "never a terminal stop",  # waiting is a wake-up, not a stop
        "disarm it before any deliberate stop",  # else the operator's own pause is undone
        "wait for every one to report",  # a reviewer that has not reported found nothing?
        "may never PROMOTE",
        "Termination checklist",
        "ddflow_recover",  # a crashed agent's finished work is salvaged first
    ],
)
def test_the_loop_carries_the_rules_an_unattended_run_depends_on(must_say):
    assert must_say in P.render(P.resolve_command("implement"), scope="")


def test_every_tool_the_loop_names_exists():
    """A prompt naming a tool the server does not have sends the agent looking for it,
    and it reads as a complete instruction while it does."""
    import re

    from ddflow.surfaces.mcp import TOOLS

    text = P.resolve_command("implement").text
    named = set(re.findall(r"`(ddflow_[a-z_]+)`", text))
    assert named, "the loop names no tools at all"
    assert named <= set(TOOLS), f"unknown tools: {sorted(named - set(TOOLS))}"


def test_adopt_for_claude_writes_implement_which_hands_the_command_to_loop(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    text = (repo / CMD).read_text()
    assert text.startswith("---\n") and "description:" in text.split("---")[1]
    assert "/loop" in text and "$ARGUMENTS" in text
    assert "`name: implement`" in text
    # the delta it sends the agent to must be one adopt actually wrote
    assert (repo / "docs/ddflow/drivers/deltas/claude-code.md").is_file()
    assert "docs/ddflow/drivers/deltas/claude-code.md" in text


def test_adopt_writes_no_slash_command_for_an_agent_that_has_none(repo):
    run_cli(repo, "adopt", "--agents", "cursor")
    assert not (repo / CMD).exists()
    assert set(AGENT_COMMANDS) == {"claude"}


def test_adopt_keeps_a_projects_own_implement_command(repo):
    """The motivating case: a project adopting mid-stream already HAS an /implement, and
    it is the one workflow that project actually runs."""
    ours = "---\ndescription: our phase loop\n---\nRun: `/loop @.claude/prompts/driver.md`\n"
    (repo / ".claude/commands").mkdir(parents=True)
    (repo / CMD).write_text(ours)
    code, out, err = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0, err
    assert (repo / CMD).read_text() == ours
    assert f"kept {CMD}" in out


def test_adopt_upgrades_its_own_managed_copy_and_is_idempotent(repo):
    (repo / ".claude/commands").mkdir(parents=True)
    (repo / CMD).write_text(f"---\n---\n{MANAGED_MARK} -->\nan older ddflow version\n")
    run_cli(repo, "adopt", "--agents", "claude")
    first = (repo / CMD).read_text()
    assert "an older ddflow version" not in first and "/loop" in first
    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0 and f"{CMD} is current" in out
    assert (repo / CMD).read_text() == first
