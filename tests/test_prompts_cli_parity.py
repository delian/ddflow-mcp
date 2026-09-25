"""The command templates existed on MCP and were invisible from the CLI.

`ddflow/services/prompts.py` holds two registries. `TEMPLATE_NAMES` is the machinery —
the review system prompt, the gate instruction, the MCP handshake. `COMMANDS` is the
workflow library: `bug-hunt`, `code-deduplication`, `import-existing-project`,
`research-companions`. Both are operator-editable text with the same override
precedence.

The MCP surface serves both: `prompts/list` and `prompts/get` read `P.COMMANDS`. The CLI
served only the first — `list_all` walks `TEMPLATE_NAMES`, and `prompts show` raises
`unknown template` for any command name. So half the prompt library was reachable from
an agent and unreachable from a terminal.

This is the parity direction the existing test does not look in. `test_mcp_parity` asks
"is every CLI command exposed on MCP", which is the direction that matters for an agent.
Nothing asked the reverse, so a capability could sit on one surface indefinitely.

Found by documenting `ddflow prompts show research-companions` in the README and then
running it. The README was committed one commit before this test existed, which is the
honest version of how this was noticed: the doc was written from what the tool *should*
do, and only checking turned it into a finding.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import prompts as P

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


# -- list ------------------------------------------------------------------------------


def test_prompts_list_names_every_command(repo):
    """The probe. Before the fix this printed five templates and none of the commands,
    so the workflow library was undiscoverable from a terminal."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "prompts", "list")
    assert code == OK, err
    missing = [n for n in P.COMMANDS if n not in out]
    assert not missing, f"`prompts list` does not mention {missing}"


def test_prompts_list_still_names_every_template(repo):
    """The other half, so the fix cannot quietly replace one registry with the other."""
    run_cli(repo, "init")
    _code, out, _err = run_cli(repo, "prompts", "list")
    missing = [n for n in P.TEMPLATE_NAMES if n not in out]
    assert not missing, f"`prompts list` stopped mentioning {missing}"


def test_the_two_kinds_are_distinguishable_in_the_listing(repo):
    """They are edited the same way and used for entirely different things. A flat list
    of eleven names invites `prompts show mcp_instructions` expecting a workflow."""
    run_cli(repo, "init")
    _code, out, _err = run_cli(repo, "prompts", "list")
    assert "Workflow commands" in out or "Commands" in out, out


def test_the_json_listing_carries_the_kind(repo):
    """An agent reading JSON has to be able to tell them apart without parsing headings."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "--json", "prompts", "list")
    assert code == OK, err
    rows = json.loads(out)
    by_name = {r["name"]: r for r in rows}
    assert by_name["mcp_instructions"]["kind"] == "template"
    assert by_name["bug-hunt"]["kind"] == "command"
    assert set(by_name) == set(P.TEMPLATE_NAMES) | set(P.COMMANDS)


# -- show ------------------------------------------------------------------------------


def test_prompts_show_resolves_a_command(repo):
    """The README documents this exact invocation. It raised `unknown template`."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "prompts", "show", "research-companions")
    assert code == OK, err
    assert len(out.strip()) > 200, "resolved to something empty"


def test_prompts_show_still_resolves_a_template(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "prompts", "show", "mcp_instructions")
    assert code == OK, err
    assert out.strip()


def test_an_unknown_name_names_BOTH_registries(repo):
    """The old message listed the five templates only, so a user who typed a command
    name correctly was told their correct name was not a known one — and shown a list
    that did not contain it."""
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "prompts", "show", "no-such-prompt")
    assert code == FAIL
    assert "bug-hunt" in err, f"the error does not mention the command registry: {err}"
    assert "mcp_instructions" in err, f"the error does not mention the templates: {err}"


def test_a_project_override_of_a_command_wins_on_the_CLI_too(repo):
    """The override path is the whole point of these being files. It worked over MCP and
    could not be checked from a terminal, which is where an operator edits it."""
    run_cli(repo, "init")
    local = repo / ".ddflow" / "prompts" / "commands"
    local.mkdir(parents=True, exist_ok=True)
    (local / "bug-hunt.md").write_text("MY OWN BUG HUNT")
    _code, out, _err = run_cli(repo, "prompts", "show", "bug-hunt")
    assert "MY OWN BUG HUNT" in out
    _code, listing, _err = run_cli(repo, "prompts", "list")
    assert "project" in listing, "an overridden command still reports as builtin"


# -- eject -----------------------------------------------------------------------------


def test_eject_can_copy_a_command_out_for_editing(repo):
    """`eject` is how the docs tell you to customise a prompt. It handled templates
    only, so the documented way to edit a workflow command did not exist."""
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "prompts", "eject", "research-companions")
    assert code == OK, err
    dest = repo / ".ddflow" / "prompts" / "commands" / "research-companions.md"
    assert dest.is_file(), "ejected a command but wrote no file where resolution looks"
    _code, out, _err = run_cli(repo, "prompts", "show", "research-companions")
    assert out.strip() == dest.read_text().strip()


def test_ejecting_everything_covers_both_registries(repo):
    """With no name, `eject` writes the lot. Writing only half is the silent version of
    the bug this file is about."""
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "prompts", "eject")
    assert code == OK, err
    base = repo / ".ddflow" / "prompts"
    for n in P.TEMPLATE_NAMES:
        assert (base / f"{n}.md").is_file(), f"template {n} was not ejected"
    for n in P.COMMANDS:
        assert (base / "commands" / f"{n}.md").is_file(), f"command {n} was not ejected"


# -- the ratchet that keeps it true ----------------------------------------------------


def test_every_prompt_on_MCP_is_reachable_from_the_CLI(repo):
    """The parity direction nothing checked.

    `test_mcp_parity` asks whether every CLI command is exposed on MCP, which is the
    direction that matters for an agent. The reverse matters for the operator, and
    without a check a capability can sit on one surface indefinitely — which is exactly
    how the whole command registry came to be terminal-invisible.
    """
    run_cli(repo, "init")
    _code, out, _err = run_cli(repo, "--json", "prompts", "list")
    on_cli = {r["name"] for r in json.loads(out)}
    on_mcp = set(P.COMMANDS) | set(P.TEMPLATE_NAMES)
    assert on_mcp <= on_cli, f"reachable over MCP and not from the CLI: {sorted(on_mcp - on_cli)}"


def test_the_readme_only_documents_prompt_names_that_resolve(repo):
    """This bug reached a commit as a README line documenting a command that errored.

    A doc naming a prompt that does not exist is worse than no doc: whoever finds
    nothing reads the code, and whoever finds a wrong invocation trusts it.
    """
    import re

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    known = set(P.TEMPLATE_NAMES) | set(P.COMMANDS)
    named = set(re.findall(r"ddflow[ _]prompts[ _](?:show|eject)\s+([a-z0-9][a-z0-9_-]*)", readme))
    unknown = {n for n in named if n not in known}
    assert not unknown, f"README names prompt(s) that do not exist: {sorted(unknown)}"
