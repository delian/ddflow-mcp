"""The surfaces hand their command and tool tables to the stale-references migration
(B-uni-compat-migrations.5-refs-surface).

`services.migrations.refs` rewrites the deprecated names ddflow wrote into its own regions,
but only judges names by a vocabulary a surface provides. Before this, no surface did, so in
a real process `ddflow upgrade` found nothing. The CLI provides its parser and the tool
table when it is imported; the MCP server, whose tool table is api-free, provides the table
when it first reaches the api.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import run_cli

import ddflow.surfaces.cli
from ddflow import api as A
from ddflow.api import refs as AR
from ddflow.infra.fsio import Managed
from ddflow.services.migrations import refs as MR
from ddflow.surfaces.registry import Alias

OLD = "oldclaim"


def _parser_with_an_old_name() -> argparse.ArgumentParser:
    """The real CLI parser, plus the old name a rename would leave: `oldclaim` for `claim`."""
    parser = ddflow.surfaces.cli.build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    action._compat = {OLD: Alias("command", OLD, "claim", "0.2.0")}
    return parser


@pytest.fixture
def project(repo: Path):
    assert run_cli(repo, "init")[0] == 0
    body = f"Run `ddflow {OLD} T1`, then release it.\n"
    (repo / "AGENTS.md").write_text(
        f"# Rules\n\nmine: `ddflow {OLD}`\n\n" + Managed("rules/work-queue").render(body) + "\n"
    )
    return repo


@pytest.fixture
def registered():
    saved = dict(AR._registered)
    yield
    AR._registered.update(saved)
    AR.provide_upgrade_vocabulary()  # re-register what the process had


def test_importing_the_cli_registers_the_command_and_tool_tables() -> None:
    code = (
        "import ddflow.surfaces.cli\n"
        "from ddflow.services.migrations import refs as R\n"
        "v = R._vocabulary()\n"
        "print(v is not None and ('claim',) in v.commands and 'ddflow_next' in v.tools"
        " and v.check_commands and v.check_tools)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.stdout.strip() == "True", out.stderr


def test_the_mcp_server_registers_the_tools_when_it_first_reaches_the_api() -> None:
    code = (
        "import ddflow.surfaces.tools as T\n"
        "from ddflow.services.migrations import refs as R\n"
        "print(R._vocabulary())\n"
        "T._common._api()\n"
        "v = R._vocabulary()\n"
        "print(v is not None and 'ddflow_next' in v.tools and v.check_tools"
        " and not v.check_commands)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.stdout.split() == ["None", "True"], out.stdout + out.stderr


def test_a_later_registration_adds_to_an_earlier_one(registered) -> None:
    AR._registered.update(parser=None, tools=None)
    AR.provide_upgrade_vocabulary(None, {"ddflow_next": {}})
    AR.provide_upgrade_vocabulary(_parser_with_an_old_name)
    vocab = MR._vocabulary()
    assert vocab.check_commands and vocab.check_tools and "ddflow_next" in vocab.tools
    assert vocab.command_aliases[(OLD,)].new == "claim"


def test_upgrade_lists_and_rewrites_a_deprecated_name_in_a_managed_region(
    project: Path, registered
) -> None:
    AR._registered.update(parser=None, tools=None)
    AR.provide_upgrade_vocabulary(_parser_with_an_old_name)

    plan = A.upgrade(project, plan=True)
    assert OLD in plan.data["text"] and "AGENTS.md" in plan.data["text"], plan.data["text"]

    out = A.upgrade(project, apply="migrations")
    assert out.exit == 0, out.reason
    text = (project / "AGENTS.md").read_text()
    assert f"mine: `ddflow {OLD}`" in text, "the project's own text is not touched"
    assert "Run `ddflow claim T1`" in text and f"ddflow {OLD} T1" not in text, text


def test_a_failed_registration_is_retried_by_the_next_api_call(monkeypatch) -> None:
    from ddflow.surfaces.tools import _common

    calls: list[int] = []

    def boom(parser=None, tools=None) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")

    monkeypatch.setattr(AR, "provide_upgrade_vocabulary", boom)
    monkeypatch.setitem(_common._upgrade_vocabulary, "provided", False)
    with pytest.raises(RuntimeError):
        _common._api()
    assert _common._upgrade_vocabulary["provided"] is False
    _common._api()
    assert len(calls) == 2 and _common._upgrade_vocabulary["provided"] is True
