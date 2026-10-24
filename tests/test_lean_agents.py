"""Generated lean Claude Code agent definitions (LS-agents, P-lean-start).

A subagent that names its tools pays for those schemas only: measured 25.4K -> 10.4K on the
first turn against an agent that inherits everything (docs/ddflow/research/lean-start.md,
lever 1). `ddflow adopt --agents claude` writes one definition per role under
`.claude/agents/`, in a managed region, so a re-adopt refreshes it and a hand edit is kept.
Enforcement stays in hooks, leases and gates; a definition only narrows what the role is
handed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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
        from ddflow.surfaces.tools.families import FAMILIES

        unknown = sorted(set(names) - set(TOOLS) - set(FAMILIES))
        assert not unknown, unknown
        bad = {n for n in names if n.removeprefix("ddflow_") in NEVER}
        assert not bad, f"{role} is handed an operator tool: {sorted(bad)}"


def test_the_roles_are_narrowed_the_way_their_work_is():
    r, i, v = ({t for t in LA._tools_line(x) if not t.startswith("mcp__")} for x in ROLES)
    assert not ({"Edit", "Write", "NotebookEdit"} & (r | v)), "researcher/reviewer must not edit"
    assert {"Edit", "Write"} <= i
    assert "ddflow_claim" in LA.ddflow_tools("implementer")
    assert "ddflow_claim" not in LA.ddflow_tools("researcher")
    assert "ddflow_review" in LA.ddflow_tools("reviewer")
    assert "ddflow_complete" not in LA.ddflow_tools("reviewer")


def test_the_body_is_a_charter_pointer_not_a_manual():
    for role in ROLES:
        body = LA.template_text(role).split("\n---\n", 1)[1]
        assert len(body.encode()) <= 1200, (role, len(body.encode()))
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
    code, _out, err = run_cli(repo, "adopt", "--agents", "cursor")
    assert code == 0, err
    assert (repo / "AGENTS.md").is_file(), "the adopt itself ran"
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


def test_adopt_refreshes_a_hand_edited_definition_and_saves_the_edit(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    path = repo / FILES["implementer"]
    path.write_text(path.read_text().replace("Call `ddflow_brief` first", "Call it whenever", 1))
    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0 and "your edits are kept in" in out, out
    assert "Call `ddflow_brief` first" in path.read_text()
    aside = path.with_name(path.name + ".local-edits")
    assert aside.is_file() and "Call it whenever" in aside.read_text(), "the hand edit was lost"
    assert LA.states(repo) == {}


def test_the_frontmatter_is_valid_yaml_scalars():
    """An unquoted `: ` inside a plain scalar makes Claude Code refuse the definition."""
    for role in ROLES:
        head = LA.template_text(role).split("\n---\n", 1)[0]
        for line in head.splitlines():
            key, _, val = line.partition(":")
            if key in ("description",):
                assert val.strip().startswith('"') and val.strip().endswith('"'), line


def test_every_role_is_told_to_file_ddflow_defects_it_meets():
    for role in ROLES:
        body = LA.template_text(role).split("\n---\n", 1)[1]
        for must in ("ddflow_bug_found", "already corrected", "before your context is compacted"):
            assert must in body, (role, must)
        assert "ddflow_bug_found" in LA.ddflow_tools(role), role


def test_an_unadopted_project_has_no_drift(repo):
    assert LA.states(repo) == {} and LA.drift_notes(repo) == []


def test_doctor_names_a_drifted_definition(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    path = repo / FILES["reviewer"]
    path.write_text(path.read_text().replace("Call `ddflow_brief` first", "Call it whenever", 1))
    _code, out, _ = run_cli(repo, "doctor", "--json")
    notes = json.loads(out)["notes"]
    assert any("lean agent definitions differ" in n and FILES["reviewer"] in n for n in notes), (
        notes
    )


def _allowed(role):
    return set(LA.ddflow_tools(role))


@pytest.mark.parametrize("profile", ["lean", "families", "hot+families"])
@pytest.mark.parametrize("role", ROLES)
def test_a_role_can_reach_what_its_work_needs_under_every_profile(role, profile):
    """The default connection is the lean PROFILE: it lists family tools (`ddflow_gate`, ...), not the
    flat names the first definitions allow-listed, so an implementer saw no gate or bug tool (bug
    B07c4286c92). Every family that serves a tool the role names must be allowed wherever the profile
    lists it."""
    from ddflow.surfaces.tools import profiles as PR
    from ddflow.surfaces.tools.families import placement

    listed = set(PR.advertised_names(profile))
    needed = {placement(t).family for t in LA.ddflow_tools(role) if t in TOOLS}
    missing = sorted(f for f in needed if f in listed and f not in _allowed(role))
    assert not missing, f"{role} on {profile}: listed family tools not allow-listed: {missing}"


def test_the_family_names_a_role_allows_exist():
    from ddflow.surfaces.tools.families import FAMILIES

    for role in ROLES:
        for name in LA.ddflow_tools(role):
            assert name in TOOLS or name in FAMILIES, (role, name)


def test_a_role_is_told_to_fall_back_to_the_cli_for_a_tool_its_profile_does_not_list():
    for role in ROLES:
        body = LA.template_text(role).split("\n---\n", 1)[1]
        assert "not listed" in body and "`ddflow " in body, role


def _stamp_head(text: str) -> str:
    import re

    return re.search(r"ddflow:begin agents/\S+ .*?head=(\w+)", text).group(1)


def test_a_new_ddflow_refreshes_the_front_matter_it_wrote_and_keeps_one_the_operator_edited(repo):
    """The tools list is front matter, outside the managed region: it must follow the template or a
    ddflow upgrade never reaches an existing definition (bug B07c4286c92), yet a hand edit of it
    (a `model:` line) is the operator's and survives."""
    from ddflow.infra.fsio import Managed

    run_cli(repo, "adopt", "--agents", "claude")
    path = repo / FILES["reviewer"]
    fresh = path.read_text()
    assert _stamp_head(fresh), "the region records the digest of the front matter ddflow wrote"
    # an older ddflow's file: other tools line, region stamped with that older head
    from ddflow.core.digest import content_digest

    head, _, _rest = fresh.partition("\n---\n")
    old_head = head.replace("tools: ", "tools: Read, ", 1) + "\n---\n"
    region = Managed("agents/ddflow-reviewer")
    at = region._region().find(fresh)
    body = fresh[at[1] : at[2]]
    path.write_text(
        old_head + region.render(body, extra=(("head", content_digest(old_head, length=12)),))
    )
    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0 and path.read_text() == fresh, "the unedited old front matter was refreshed"
    # a hand edit of the front matter is kept
    edited = fresh.replace("tools: ", "model: haiku\ntools: ", 1)
    path.write_text(edited)
    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0 and "model: haiku" in path.read_text(), (
        "the operator's front matter edit survived"
    )
    assert "front matter edited by hand: kept" in out, out


def test_a_definition_from_before_the_head_stamp_is_refreshed_and_the_old_one_kept_aside(repo):
    from ddflow.infra.fsio import Managed

    run_cli(repo, "adopt", "--agents", "claude")
    path = repo / FILES["implementer"]
    fresh = path.read_text()
    head = fresh.partition("\n---\n")[0] + "\n---\n"
    old_head = head.replace("tools: ", "tools: Read, ", 1)
    region = Managed("agents/ddflow-implementer")
    at = region._region().find(fresh)
    path.write_text(old_head + region.render(fresh[at[1] : at[2]]))  # no head attribute
    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0 and path.read_text() == fresh
    aside = path.with_name(path.name + ".local-edits")
    assert aside.is_file() and "tools: Read, " in aside.read_text(), out
    assert f"the previous file is kept in {aside.name}" in out, out
