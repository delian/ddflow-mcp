"""The `initialize` handshake's instruction text, and the re-instruction footer.

Gathered defensively: `_instruction_vars` runs inside the handshake, which must succeed
even in a repository that is broken or not adopted, so every lookup fails to its own
default instead of taking the handshake down. Split out of the old `surfaces/mcp.py`
engine with `_obligation_footer`, the reinstruct cadence.
"""

from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any

from ddflow.core import measuring

from ...config import Config
from ..tools._common import _api
from ..tools.tiers import DEFAULT_TIER, tier_note
from ._core import _surf


def _obligation_footer(server) -> str:
    """The re-instruction footer, or "" — see `services/obligations.py` and `[reinstruct]`.

    Two guards, in this order, because the cheap one comes first: the CADENCE is counters on
    the connection and costs nothing, and only once it opens does this fold the log. Both
    `every_calls` and `every_seconds` must be satisfied, so a burst of calls does not
    produce a burst of footers.

    Swallows everything. A footer is a courtesy on top of an answer the caller asked for,
    and a broken courtesy must never turn a successful tool call into a failure.
    """
    try:
        cfg = Config.load(server.repo)
        if not cfg.reinstruct.enabled:
            return ""
        server._calls_since_footer += 1
        now = time.time()
        if server._calls_since_footer < cfg.reinstruct.every_calls:
            return ""
        if now - server._last_footer_at < cfg.reinstruct.every_seconds:
            return ""
        text = _surf().obligation_footer(server.repo, cfg)
        if not text:
            # Nothing to say. The counters are NOT reset: a quiet project should not have
            # to wait another twelve calls once something does come up.
            return ""
        server._calls_since_footer = 0
        server._last_footer_at = now
        return text
    except Exception:
        return ""


_VAR_DEFAULTS: dict[str, Any] = {
    "setup_todo": [],
    "companions": [],
    "missing_companions": [],
    "unregistered_companions": [],
    "uninstalled_companions": [],
    # Seeded here, with every sibling, because the block that computes these sits
    # AFTER the `if not adopted: return v` below -- so an unadopted repository got
    # a variable set the template could not render, and the whole handshake became
    # "ddflow's instruction template could not be loaded". The template guards the
    # use (`{% if adopted %}`), but a guard is only as good as the engine's
    # willingness to short-circuit, and one of the two did not. Defaults do not
    # depend on which branch ran.
    # The rules surface: present, stripped, drifted or gone. Seeded with its siblings
    # so the unadopted path renders — the lesson B153 cost a broken handshake for every
    # first-time user.
    "rules_drift": [],
    "unchecked_companions": [],
    "actionable_companions": [],
    "gate_gaps": [],
    "recoverable": 0,
    "ready": 0,
    "running": 0,
    "blocked": 0,
    "open_bugs": 0,
    "loops": 0,
    "task_pipeline": [],
    # Set by `_instructions` from the connection's tier; seeded here so the template's
    # variable contract holds on every path.
    "tool_tier_note": "",
    "require_outcome": True,
    "importable": 0,
    "queue_is_empty": True,
    "imported_total": 0,
    "imported_no_globs": 0,
    "imported_shipped_drift": 0,
}


#: The readers `_instruction_vars` runs, in order; each is `api.surf_mcp.READERS[<name>]` (`pipeline` is the engine's own).
_FILLERS = ("rules_drift", "pipeline", "unit_test_todo", "reviewer_todo", "companions", "queue")


def _instruction_vars(repo: Path, agent: str = "") -> dict[str, Any]:
    """Everything `mcp_instructions.md` can render from.

    Gathered defensively: this runs inside the `initialize` handshake, which must
    succeed even in a repository that is broken, half-configured or not adopted at all.
    Every lookup that can fail contributes its own default rather than taking the whole
    handshake down, because a server that refuses to start cannot tell anyone why.

    It also must not WRITE anything to the project — a handshake that adopts the repository
    is the bug this file already fixed once, and `Store` learned the same lesson separately.
    (The one exception is the ready count: `plan_for` reads the waiters' registry, which
    prunes an expired entry under `.ddflow/local/`, git-ignored advisory state that the next
    `next` or `wait` would prune the same way.)
    """
    adopted = (repo / ".ddflow" / "config.toml").is_file()
    v: dict[str, Any] = copy.deepcopy(_VAR_DEFAULTS)
    v["adopted"] = adopted
    # Cheap enough for a handshake: `glob` on a handful of known paths, no parsing.
    # The point is only to know whether to OFFER the import, not to do it.
    try:
        v["importable"] = _surf().importable_count(repo)
    except Exception:
        pass
    if not adopted:
        return v

    try:
        cfg = Config.load(repo)
    except Exception:
        return v
    # Each block is independent, and a failure in one must not cost the others: a
    # project with a bad reviewer block should still be told what is ready to work. The
    # rules surface goes first: it is the one fact that decides whether the agent has any
    # project rules at all. Each reader is looked up inside its own guard, so a domain
    # layer that cannot even be imported costs its block and no other.
    for name in _FILLERS:
        try:
            fill = _fill_pipeline if name == "pipeline" else _surf().READERS[name]
            fill(v, repo, cfg, agent)
        except Exception:
            pass
    return v


def _fill_pipeline(v: dict[str, Any], repo: Path, cfg: Config, agent: str) -> None:
    """The gate pipeline the template names, and whether a gate outcome is required."""
    v["task_pipeline"] = list(cfg.gates.task_pipeline)
    v["require_outcome"] = bool(cfg.gates.require_outcome)


def _upgrade_line(repo: Path, agent: str) -> str:
    """What the handshake says about an upgrade, set off by a blank line, or "".

    First the start report (`services.upgrade_start`, `[upgrade].on_start`): ddflow newer than
    the project -> what the start did and the proposal the operator is asked about. Else the
    one-line notice, said once per version on this machine, per project
    (`services.upgrade_notice`). A courtesy that can never fail a connect."""
    if measuring.is_measuring():  # `doctor` counting the handshake must not use up a notice
        return ""
    notice = ""
    for name in ("upgrade_start", "upgrade_notice", "release_proposal"):
        try:  # each on its own: a start report that fails must not silence the notice
            notice = getattr(_api(), name)(repo, agent=agent)
        except Exception:
            continue
        if notice:
            break
    return f"\n\n{notice}" if notice else ""


def _upgrade_note(repo: Path, agent: str) -> str:
    """The upgrade line as one note for a tool result (no leading blank lines)."""
    return _upgrade_line(repo, agent).strip()


def _instructions(repo: Path, agent: str = "", tier: str = DEFAULT_TIER) -> str:
    """What the client injects into the model's context on connect.

    **State-aware on purpose.** A fixed blurb describing a workflow the project has not
    adopted is noise the model learns to skip; the useful instruction is the next
    concrete action, and that depends on whether `.ddflow/` exists, whether a test
    command is set, whether a reviewer and the companion tools are configured, and
    whether anything is waiting to be recovered. This is the only place the server gets
    to speak unprompted, so it says the one thing that is true right now.

    **And it is a TEMPLATE, not a string literal.** Everything it says — the workflow,
    the reporting duties, which companion tools to reach for — is
    `templates/prompts/mcp_instructions.md`, overridable per project
    (`.ddflow/prompts/mcp_instructions.md`) or per config (`[prompts]
    mcp_instructions`). That is the difference between a tool whose behaviour you
    configure and one you have to fork.
    """
    # One load of the project for the whole handshake (B-mcp-initialize-folds-log): the queue
    # counts and each upgrade reader share it instead of folding the log once apiece.
    stable, volatile = _instructions_parts(repo, agent, tier)
    return stable + volatile


def _instructions_parts(repo: Path, agent: str, tier: str) -> tuple[str, str]:
    """`(stable, upgrade line)`: the text the interface snapshot caches, and the volatile
    upgrade notice a snapshot-served handshake says on the first tool result instead
    (SU-init-fast-hook)."""
    with _surf().load_scope():
        return _instructions_text(repo, agent, tier), _upgrade_line(repo, agent)


def _instructions_text(repo: Path, agent: str, tier: str) -> str:
    """The rendered template, inside the caller's load scope (no upgrade line)."""
    vars_ = _instruction_vars(repo, agent)
    vars_["tool_tier_note"] = tier_note(tier, names=False)
    overrides: dict[str, str] = {}
    if vars_["adopted"]:
        try:
            path = _surf().instructions_override(repo)
            if path:
                overrides["mcp_instructions"] = path
        except Exception:
            pass
    try:
        text = _surf().render_instructions(repo, overrides, vars_)
    except _surf().TemplateError as exc:
        # A broken override must not silence the server: say what is wrong, in the one
        # place the operator will see it, and still hand over the essentials.
        text = (
            f"ddflow's instruction template could not be loaded: {exc}\n\n"
            "Call `ddflow_brief` for the state of the queue, and `ddflow_prompts` to "
            "inspect the template configuration."
        )
    return text
