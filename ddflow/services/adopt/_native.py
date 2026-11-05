"""What adopt writes for one agent beyond its MCP entry and slash commands: Claude Code's
lean role definitions, the worker MCP config, and each agent's own native rules file."""

from __future__ import annotations

from pathlib import Path

from ...config import Config
from ...infra.fsio import replace_text
from .. import leanagents as LA
from .. import workermcp as WM
from ._aider import _add_aider_read
from ._commands import _write_command
from ._launch import _launch_entry
from ._rules import native_rule_text, project_section
from ._targets import FORM_AIDER, FORM_BLOCK, FORM_WHOLE, NATIVE_RULES
from ._upsert import _upsert_block


def adopt_notes(repo: Path, cfg) -> list[str]:
    """The doctor's notes on what adopt writes for Claude Code beyond its MCP entry: the worker MCP
    config (`services.workermcp`) and the lean agent definitions (`services.leanagents`)."""
    return WM.doctor_notes(cfg) + LA.drift_notes(repo)


def _claude_extras(repo: Path, key: str, templates: Path, launch: str, image: str) -> list[str]:
    """What adopt writes for Claude Code beyond its MCP entry and commands (P-lean-start): the lean
    role definitions (`services.leanagents`, each in its managed region) and the worker MCP config
    (`services.workermcp`) with the line that says how to start a worker on it."""
    out = [
        _write_command(repo, dst, templates / src, "agents")
        for dst, src in LA.agent_files(key).items()
    ]
    if key == LA.HARNESS:
        mcp = Config.load(repo).mcp
        tier = mcp.worker_tools
        entry = _launch_entry(launch, image)
        wrote = WM.write(repo, entry, tier)
        out += [wrote] + ([WM.recipe(tier=tier)] if not wrote.endswith("is current") else [])
        out += WM.write_lanes(repo, entry, mcp.lane_tools)
    return out


def _write_native_rule(repo: Path, key: str, docs_dir: str = "docs/ddflow") -> str:
    """Write the agent's own rules file, for agents whose native surface outranks
    `AGENTS.md`.

    The body is the SAME managed block, so there is one source for the project text and
    the two copies cannot say different things. Only the frontmatter differs, and it is
    what makes the rule bind: `alwaysApply: true`, because claim-before-you-edit is not
    a rule that should depend on the model choosing to load it.
    """
    rule = NATIVE_RULES[key]
    path = repo / rule.path
    path.parent.mkdir(parents=True, exist_ok=True)
    if rule.form == FORM_WHOLE:
        # The whole file is ours: the frontmatter has to come FIRST for the rule to bind,
        # so there is nowhere to put a marker above it.
        replace_text(path, native_rule_text(docs_dir))
        return f"wrote {rule.path} (always-applied project rule)"
    if rule.form == FORM_BLOCK:
        # A file the project may already own (`QWEN.md`, `replit.md`, `.goosehints`), so a
        # managed block rather than a wholesale write — the operator's own rules stay.
        return _upsert_block(path, project_section(docs_dir), rule.path)
    if rule.form == FORM_AIDER:
        return _add_aider_read(path)
    raise ValueError(f"unknown native rules form {rule.form!r}")
