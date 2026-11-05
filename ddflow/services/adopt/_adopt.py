"""The `adopt` entry point: one command that installs the workflow into a project."""

from __future__ import annotations

from pathlib import Path

from ...infra import paths
from ._commands import _install_prompt_hooks, _write_harness_files
from ._docs import _backup_docs, _write_driver
from ._launch import _launch_entry, _register_mcp
from ._native import _claude_extras, _write_native_rule
from ._rules import project_section
from ._scaffold import init_files
from ._targets import AGENT_TARGETS, NATIVE_RULES
from ._upsert import _upsert_block


def adopt(
    repo: Path,
    agents: list[str],
    *,
    package_dir: Path | None = None,
    docs_dir: str = "docs/ddflow",
    force: bool = False,
    install_hooks: bool = True,
    launch: str = "auto",
    image: str = "ghcr.io/OWNER/ddflow:latest",
) -> list[str]:
    from .. import enforce as E  # deferred: enforce imports this module

    repo = Path(repo)
    actions: list[str] = []
    # Templates live INSIDE the package (`ddflow/templates/`), not beside it, so they
    # ship in the wheel. They were originally a sibling directory, which worked from a
    # source checkout and then raised FileNotFoundError for every installed user --
    # the classic packaging bug that only a real install reproduces.
    templates = Path(package_dir) / "templates" if package_dir else paths.templates_dir()
    if not (templates / "drivers" / "implement-phase.md").is_file():
        raise FileNotFoundError(
            f"driver templates are missing from {templates}. This is a packaging fault, "
            f"not a configuration one: reinstall ddflow-mcp."
        )

    # Validate EVERY name before writing ANYTHING. The check used to sit inside the copy
    # loop, after the canonical driver was already written, so `--agents claude-code`
    # left a file behind and then exited 1 -- a partial adoption reported as a failure.
    unknown = [k for k in agents if k not in AGENT_TARGETS]
    if unknown:
        raise ValueError(
            f"unknown agent(s) {', '.join(map(repr, unknown))}; known: {', '.join(AGENT_TARGETS)}"
        )

    actions.extend(init_files(repo))
    if saved := _backup_docs(repo, docs_dir, package_dir, None):  # the originals first
        actions.append(f"saved the originals in {saved}")

    drivers_dst = repo / docs_dir / "drivers"
    drivers_dst.mkdir(parents=True, exist_ok=True)
    rel = f"{docs_dir}/drivers/implement-phase.md"
    actions.append(
        _write_driver(
            drivers_dst / "implement-phase.md", templates / "drivers" / "implement-phase.md", rel
        )
    )

    (drivers_dst / "deltas").mkdir(exist_ok=True)
    for key in agents:
        delta = AGENT_TARGETS[key].delta
        rel = f"{docs_dir}/drivers/deltas/{delta}"
        actions.append(
            _write_driver(
                drivers_dst / "deltas" / delta, templates / "drivers" / "deltas" / delta, rel
            )
        )

    section = project_section(docs_dir)
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = repo / name
        if name == "CLAUDE.md" and not path.exists() and "claude" not in agents:
            continue
        actions.append(_upsert_block(path, section, name))

    if install_hooks:
        actions.append(E.install(repo))
        actions.extend(_install_prompt_hooks(repo, agents))
    for key in agents:
        actions.append(_register_mcp(repo, key, launch=launch, image=image))
        if key in NATIVE_RULES:
            actions.append(_write_native_rule(repo, key, docs_dir))
        actions.extend(_write_harness_files(repo, key, templates))
        actions.extend(_claude_extras(repo, key, templates, launch, image))
    # Only when a line written here carries a path: a uvx or docker entry has none, and
    # the hooks' own install message already says it for the hooks.
    entry = _launch_entry(launch, image)
    line = (
        f'PYTHONPATH="{entry["env"]["PYTHONPATH"]}" {entry["command"]}' if entry.get("env") else ""
    )
    if line and (note := E.redirect_note(line)) and note not in "\n".join(actions):
        actions.append(note)
    return actions
