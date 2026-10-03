"""The brief names the project's own skills and rules that bear on the task."""

from __future__ import annotations

from pathlib import Path

from ddflow.services import skills as SK


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".claude/skills/db-migrations").mkdir(parents=True)
    (tmp_path / ".claude/skills/db-migrations/SKILL.md").write_text(
        "---\nname: db-migrations\ndescription: Write and review database schema migrations\n---\n"
        "Use alembic. SECRET-BODY-MARKER\n"
    )
    (tmp_path / ".claude/skills/poetry-style").mkdir(parents=True)
    (tmp_path / ".claude/skills/poetry-style/SKILL.md").write_text(
        "---\nname: poetry-style\ndescription: Compose sonnets and haiku verses\n---\nRhyme.\n"
    )
    (tmp_path / ".claude/commands").mkdir()
    (tmp_path / ".claude/commands/deploy.md").write_text("Deploy the service to staging\n")
    (tmp_path / ".cursor/rules").mkdir(parents=True)
    (tmp_path / ".cursor/rules/api.mdc").write_text(
        "---\ndescription: REST api conventions\n---\nx\n"
    )
    (tmp_path / "AGENTS.md").write_text(
        "# Team rules\nPrefer small functions.\n<!-- ddflow:begin -->\nddflow managed text\n"
        "<!-- ddflow:end -->\n"
    )
    return tmp_path


def test_inventory_finds_every_source(tmp_path):
    names = {e.name for e in SK.inventory(_repo(tmp_path))}
    assert {"db-migrations", "poetry-style", "deploy", "api", "AGENTS.md"} <= names


def test_matching_task_surfaces_relevant_skill_only(tmp_path):
    repo = _repo(tmp_path)
    hit = SK.relevant(repo, "add a database schema migration for users")
    assert hit and hit[0].name == "db-migrations"
    assert "poetry-style" not in {e.name for e in hit}
    assert SK.relevant(repo, "zebra quokka") == []


def test_ddflow_block_is_excluded_and_content_never_copied(tmp_path):
    repo = _repo(tmp_path)
    assert SK.relevant(repo, "ddflow managed text") == []
    line = SK.relevant(repo, "database migration")[0].line()
    assert "SECRET-BODY-MARKER" not in line and ".claude/skills/db-migrations/SKILL.md" in line


def test_no_files_no_section(tmp_path):
    assert SK.relevant(tmp_path, "anything at all") == []


def test_brief_section_appears_for_matching_task_only(repo):
    from conftest import run_cli

    _repo(repo)
    for tid, title in (("T1", "database schema migration"), ("T2", "zebra quokka wrangling")):
        code, out, err = run_cli(repo, "task", "add", tid, "--title", title, "--globs", f"{tid}.py")
        assert code == 0, out + err
    code, out, _ = run_cli(repo, "brief", "--item", "T1")
    assert "Project skills and rules that bear on this task" in out
    assert ".claude/skills/db-migrations/SKILL.md" in out and "poetry-style" not in out
    code, out, _ = run_cli(repo, "brief", "--item", "T2")
    assert "Project skills and rules" not in out
