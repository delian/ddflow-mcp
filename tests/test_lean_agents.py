"""Generated lean Claude Code agent definitions (LS-agents, P-lean-start).

A subagent that names its tools pays for those schemas only: measured 25.4K -> 10.4K on the
first turn against an agent that inherits everything (docs/ddflow/research/lean-start.md,
lever 1). `ddflow adopt --agents claude` writes one definition per role under
`.claude/agents/`, in a managed region, so a re-adopt refreshes it and a hand edit is kept.
Enforcement stays in hooks, leases and gates; a definition only narrows what the role is
handed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import leanagents as LA
from ddflow.surfaces.mcp import TOOLS

ROLES = ("researcher", "implementer", "reviewer")
FILES = {r: f".claude/agents/ddflow-{r}.md" for r in ROLES}
#: Tools that are the operator's act or change policy: no role definition may name them.
NEVER = {"gate_skip", "configure", "upgrade", "rule_remove", "rule_edit", "rule_add", "abandon"}


def _front(text: str) -> dict[str, object]:
    assert text.startswith("---\n"), text[:40]
    block = text.split("\n---\n", 1)[0].removeprefix("---\n")
    out: dict[str, object] = {}
    key = ""
    for line in block.splitlines():
        if line.startswith("  - ") or line.startswith("- "):
            out.setdefault(key, []).append(line.split("- ", 1)[1].strip())  # type: ignore[union-attr]
        else:
            key, _, val = line.partition(":")
            out[key.strip()] = val.strip() if val.strip() else []
    return out


def test_every_role_has_a_template_with_the_fields_claude_code_reads():
    for role in ROLES:
        text = LA.template_text(role)
        fm = _front(text)
        assert fm["name"] == f"ddflow-{role}", fm
        assert fm["description"], role
        tools = [t.strip() for t in str(fm["tools"]).split(",")]
        assert "mcp__ddflow__ddflow_brief" in tools, f"{role} cannot call ddflow_brief"
        assert fm["mcpServers"] == ["ddflow"], f"{role} must name the ddflow server"


def test_every_ddflow_tool_a_role_names_exists_and_none_is_an_operator_act():
    for role in ROLES:
        names = LA.ddflow_tools(role)
        assert names, role
        assert set(names) <= set(TOOLS), sorted(set(names) - set(TOOLS))
        bad = {n for n in names if n.removeprefix("ddflow_") in NEVER}
        assert not bad, f"{role} is handed an operator tool: {sorted(bad)}"


def test_the_roles_are_narrowed_the_way_their_work_is():
    r, i, v = (set(LA.builtin_tools(x)) for x in ROLES)
    assert not ({"Edit", "Write", "NotebookEdit"} & (r | v)), "researcher/reviewer must not edit"
    assert {"Edit", "Write"} <= i
    assert "ddflow_claim" in LA.ddflow_tools("implementer")
    assert "ddflow_claim" not in LA.ddflow_tools("researcher")
    assert "ddflow_review" in LA.ddflow_tools("reviewer")
    assert "ddflow_complete" not in LA.ddflow_tools("reviewer")


def test_the_body_is_a_charter_pointer_not_a_manual():
    for role in ROLES:
        body = LA.template_text(role).split("\n---\n", 1)[1]
        assert len(body.encode()) <= 900, (role, len(body.encode()))
        for must in ("ddflow_brief", "as_agent", "ask the operator", "unavailable"):
            assert must in body, (role, must)


def test_a_role_costs_less_than_the_core_tier_it_replaces():
    from ddflow.surfaces.startcost_texts import role_tokens, schema_tokens
    from ddflow.surfaces.tools.tiers import tier_tools

    core = schema_tokens(sorted(tier_tools("core")))
    for role in ROLES:
        cost = role_tokens(role)
        assert 0 < cost < core, (role, cost, core)


def test_adopt_for_claude_writes_the_three_definitions_and_is_idempotent(repo):
    code, out, err = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0, err
    for role, rel in FILES.items():
        text = (repo / rel).read_text()
        assert "ddflow:begin agents/ddflow-" + role in text, rel
        assert text.startswith("---\n")
    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0 and all(f"{rel} is current" in out for rel in FILES.values()), out


def test_adopt_for_another_agent_writes_no_claude_definitions(repo):
    run_cli(repo, "adopt", "--agents", "cursor")
    assert not (repo / ".claude" / "agents").exists()


def test_a_projects_own_definition_is_kept(repo):
    (repo / ".claude/agents").mkdir(parents=True)
    ours = "---\nname: ddflow-researcher\ndescription: ours\n---\nour own\n"
    (repo / FILES["researcher"]).write_text(ours)
    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0
    assert (repo / FILES["researcher"]).read_text() == ours
    assert f"kept {FILES['researcher']}" in out


def test_a_hand_edit_inside_the_region_is_reported_as_drift(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    path = repo / FILES["implementer"]
    path.write_text(path.read_text().replace("Call `ddflow_brief` first", "Call it whenever", 1))
    states = LA.states(repo)
    assert states.get(FILES["implementer"]) == "edited", states
    assert FILES["reviewer"] not in states, "a current definition is not drift"
    notes = LA.drift_notes(repo)
    assert notes and FILES["implementer"] in notes[0]


def test_an_unadopted_project_has_no_drift(repo):
    assert LA.states(repo) == {} and LA.drift_notes(repo) == []
