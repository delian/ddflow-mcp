"""`ddflow brief --role R`: the project's rules for a role, as data, under a budget
(CO-brief-roles). Every test fails against the code before the task."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from conftest import run_cli
from helpers import rpc_call, tool_json

from ddflow.core.budget import approx_tokens
from ddflow.services import brief_roles as BR
from ddflow.surfaces.mcp import Server

ROLES = ("phase-closer", "fixer", "researcher", "reviewer")


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P-x", "--title", "phase x")
    run_cli(repo, "task", "add", "T1", "--title", "task one", "--parent", "P-x", "--globs", "a.py")
    return repo


def _config(repo: Path, text: str) -> None:
    path = repo / ".ddflow" / "config.toml"
    path.write_text(path.read_text() + "\n" + text + "\n")


@pytest.mark.parametrize("role", ROLES)
def test_each_role_renders_under_its_budget(proj, role):
    code, out, _ = run_cli(proj, "brief", "--role", role)
    assert code == 0 and out.strip()
    assert approx_tokens(out) <= 600  # [roles].budget_tokens default


def test_a_tight_budget_cuts_the_pack_and_says_so(proj):
    _config(proj, "[roles]\nbudget_tokens = 60")
    code, out, _ = run_cli(proj, "brief", "--role", "phase-closer")
    assert code == 0 and approx_tokens(out) <= 60 and "cut to the budget" in out


def test_the_phase_closer_pack_carries_the_project_rules(proj):
    _, out, _ = run_cli(proj, "brief", "--role", "phase-closer", "--phase", "P-x")
    text = out.lower()
    for needle in (
        "tests first",
        "ratchet",
        "21600",
        "approval",
        "one pins recipe",
        "docs",
        "never push",
        "event log",
        "p-x",
    ):
        assert needle in text, needle


def test_the_rules_come_from_config_not_from_code(proj):
    _config(proj, '[roles]\nfixer_rules = ["Always say pineapple."]')
    _, out, _ = run_cli(proj, "brief", "--role", "fixer")
    assert "Always say pineapple." in out and "tests first" not in out.lower()


def test_a_rule_record_reference_is_rendered_from_the_record(proj):
    run_cli(proj, "rule", "add", "--id", "r-demo", "--title", "Demo rule title for roles")
    _config(proj, '[roles]\nreviewer_rules = ["rule:r-demo", "rule:r-missing"]')
    _, out, _ = run_cli(proj, "brief", "--role", "reviewer")
    assert "Demo rule title for roles" in out and "r-missing" in out and "no such rule" in out


def test_an_unknown_role_refuses_and_lists_the_roles(proj):
    code, _, err = run_cli(proj, "brief", "--role", "janitor")
    assert code == 1 and all(r in err for r in ROLES)


def test_the_role_is_an_mcp_argument_too(proj):
    body = rpc_call(Server(proj), "ddflow_brief", role="fixer")
    assert (
        "tests first" in json.dumps(body).lower() or "tests first" in str(tool_json(body)).lower()
    )


def test_the_launch_brief_can_include_a_role(proj):
    code, out, _ = run_cli(proj, "brief", "--for-agent", "helper", "T1", "--role", "fixer")
    assert code == 0 and "tests first" in out.lower()


def test_the_service_lists_its_roles():
    assert BR.ROLES == ROLES


def test_a_budget_smaller_than_the_notice_still_holds(proj):
    _config(proj, "[roles]\nbudget_tokens = 1")
    code, out, _ = run_cli(proj, "brief", "--role", "fixer")
    assert code == 0 and approx_tokens(out) <= 4 and out.startswith("# Role: fixer")
