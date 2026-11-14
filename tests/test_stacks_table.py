"""The stack table (MT-stack-build-table): per stack, the build, smoke, lock-check and test
commands as DATA, selected from the project's own files.

Falsifiers: a Rust project must not get the Python build command; a project no table entry
describes gets an explicit "none" with a next call, never a command.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.services import stacks as S


def _touch(root: Path, *names: str) -> Path:
    for n in names:
        (root / n).write_text("")
    return root


def _only(available: set[str]):
    return lambda program: f"/bin/{program}" if program in available else None


ALL_TOOLS = {"uv", "python", "npm", "cargo", "go", "mvn", "make", "git"}


@pytest.mark.parametrize(
    ("files", "stack", "build_word", "lock_word"),
    [
        (("pyproject.toml", "uv.lock"), "python", "uv", "uv lock --check"),
        (("package.json", "package-lock.json"), "node", "npm", "npm"),
        (("Cargo.toml", "Cargo.lock"), "rust", "cargo", "cargo"),
        (("go.mod", "go.sum"), "go", "go", "go"),
        (("pom.xml",), "java", "mvn", ""),
    ],
)
def test_fixture_directory_resolves_to_its_stack(tmp_path, files, stack, build_word, lock_word):
    _touch(tmp_path, *files)
    r = S.resolve(tmp_path, Config(), which=_only(ALL_TOOLS))
    assert r.stack == stack
    assert r.status == "resolved"
    assert build_word in r.steps["build"].command
    assert r.steps["smoke"].command
    if lock_word:
        assert lock_word in r.steps["lock_check"].command
    assert all(s.available for s in r.steps.values())


def test_rust_fixture_does_not_get_the_python_build_command(tmp_path):
    _touch(tmp_path, "Cargo.toml")
    r = S.resolve(tmp_path, Config(), which=_only(ALL_TOOLS))
    assert "uv" not in r.steps["build"].command
    assert "python" not in r.steps["build"].command
    assert r.steps["build"].command.startswith("cargo")


def test_unknown_stack_is_an_explicit_none_with_a_next_call(tmp_path):
    _touch(tmp_path, "README.md")
    r = S.resolve(tmp_path, Config(), which=_only(ALL_TOOLS))
    assert r.status == "none"
    assert r.stack == ""
    assert r.steps == {}
    assert "ddflow config" in r.next_call and "verify.build_cmd" in r.next_call


def test_missing_tool_is_unavailable_never_resolved(tmp_path):
    _touch(tmp_path, "Cargo.toml")
    r = S.resolve(tmp_path, Config(), which=_only(set()))
    assert r.status == "unavailable"
    assert not r.steps["build"].available
    assert "cargo" in r.detail


def test_python_without_a_uv_lock_falls_back_to_pip_tooling(tmp_path):
    _touch(tmp_path, "pyproject.toml")
    r = S.resolve(tmp_path, Config(), which=_only(ALL_TOOLS))
    assert r.stack == "python"
    assert "uv" not in r.steps["build"].command
    assert "lock_check" not in r.steps


def test_config_overrides_one_command_and_forces_a_stack(tmp_path):
    _touch(tmp_path, "Cargo.toml")
    cfg = Config()
    cfg.verify.build_cmd = "make all"
    r = S.resolve(tmp_path, cfg, which=_only(ALL_TOOLS))
    assert r.steps["build"].command == "make all"
    assert r.steps["build"].source == "config"
    assert r.steps["smoke"].source == "table"
    cfg2 = Config()
    cfg2.verify.stack = "go"
    assert S.resolve(tmp_path, cfg2, which=_only(ALL_TOOLS)).stack == "go"


def test_forced_stack_with_no_entry_is_none(tmp_path):
    cfg = Config()
    cfg.verify.stack = "cobol"
    r = S.resolve(tmp_path, cfg, which=_only(ALL_TOOLS))
    assert r.status == "none" and "cobol" in r.detail


def test_project_layer_overrides_and_adds_a_stack(tmp_path):
    _touch(tmp_path, "deno.json")
    d = tmp_path / ".ddflow" / "stacks"
    d.mkdir(parents=True)
    (d / "deno.toml").write_text(
        '[stack]\nname = "deno"\ndetect = ["deno.json"]\n[commands]\nbuild = "deno task build"\nsmoke = "deno check ."\n'
    )
    r = S.resolve(tmp_path, Config(), which=_only({"deno"}))
    assert r.stack == "deno" and r.steps["build"].command == "deno task build"


def test_broken_table_file_is_reported_not_fatal(tmp_path):
    d = tmp_path / ".ddflow" / "stacks"
    d.mkdir(parents=True)
    (d / "bad.toml").write_text("[stack\n")
    _touch(tmp_path, "Cargo.toml")
    r = S.resolve(tmp_path, Config(), which=_only(ALL_TOOLS))
    assert r.stack == "rust" and any("bad.toml" in w for w in r.warnings)


def test_every_shipped_stack_declares_the_table_fields():
    table, warnings = S.load_table()
    assert not warnings
    assert {"python", "node", "rust", "go", "java"} <= set(table)
    for name, st in table.items():
        assert st.detect, name
        assert st.commands.get("build") and st.commands.get("smoke"), name
        assert st.comment_tokens, name


# -- the four ways: file / `ddflow config` / one action on an existing MCP family / prompt ----


def test_config_route_sets_the_knob_and_the_resolution_follows(repo):
    from conftest import write_config

    _touch(repo, "Cargo.toml")
    err, _ = write_config(repo, [("verify.build_cmd", "make all")])
    assert not err
    r = S.resolve(repo, Config.load(repo), which=_only(ALL_TOOLS))
    assert r.steps["build"].command == "make all"


def test_mcp_route_is_an_action_of_ddflow_view_not_a_new_tool():
    from ddflow.surfaces.tools import family_schema as FS

    route = FS.route("ddflow_view", {"action": "stacks"})
    assert route.tool == "ddflow_stacks"
    assert route.display == "ddflow_view action=stacks"


def test_cli_reports_none_with_exit_2_and_the_next_call(repo):
    from conftest import run_cli

    code, out, _err = run_cli(repo, "stacks")
    assert code == 2
    assert "verify.build_cmd" in out


def test_cli_reports_the_python_commands_for_a_python_repo(repo):
    from conftest import run_cli

    _touch(repo, "pyproject.toml", "uv.lock")
    _code, out, _err = run_cli(repo, "stacks")
    assert "uv lock --check" in out


def test_the_help_page_carries_a_prompt_example():
    from ddflow.services import help as H

    page = (H.help_dir() / "verify.md").read_text()
    assert "ddflow stacks" in page and "verify.build_cmd" in page
