"""The skills/commands/rules inventory reads where each agent keeps them from the descriptors."""

from __future__ import annotations

from pathlib import Path

from ddflow.services import skills as SK


def _write(repo: Path, rel: str, text: str = "# a rule\n\nbody text here\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _paths(repo: Path) -> set[str]:
    return {e.path for e in SK.inventory(repo)}


def test_each_surface_the_old_inventory_hard_coded_is_still_found(repo):
    for rel in (
        ".cursor/rules/a.mdc",
        ".cursor/rules/b.md",
        ".kilo/rules/c.md",
        ".kilocode/rules/d.md",
        ".clinerules/e.md",
    ):
        _write(repo, rel)
    assert {
        ".cursor/rules/a.mdc",
        ".cursor/rules/b.md",
        ".kilo/rules/c.md",
        ".kilocode/rules/d.md",
        ".clinerules/e.md",
    } <= _paths(repo)


def test_skills_and_commands_of_other_agents_are_found_through_their_descriptors(repo):
    _write(repo, ".gemini/skills/s/SKILL.md", "---\nname: s\ndescription: a skill\n---\nbody\n")
    _write(repo, ".gemini/commands/c.md")
    found = {e.kind: e.path for e in SK.inventory(repo)}
    assert found["skill"] == ".gemini/skills/s/SKILL.md"
    assert found["command"] == ".gemini/commands/c.md"


def test_a_root_instruction_file_of_another_agent_is_a_rule(repo):
    _write(repo, "GEMINI.md")
    assert "GEMINI.md" in _paths(repo)


def test_a_path_shared_by_two_descriptors_is_listed_once(repo):
    _write(repo, ".agents/skills/x/SKILL.md", "---\nname: x\n---\nbody\n")
    assert [e.path for e in SK.inventory(repo)].count(".agents/skills/x/SKILL.md") == 1


def test_subagent_definitions_come_from_every_descriptors_dir(repo):
    _write(repo, ".claude/agents/a.md")
    _write(repo, ".gemini/agents/b.md")
    assert {p.name for p in SK.subagent_files(repo)} >= {"a.md", "b.md"}
