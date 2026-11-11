"""The lane-rotation hint: when an agent's context passes `[session].agent_rotate_tokens`.

TE-context-caps / D-TE-Q2: research 3.3 shows 12 agents with 300+ calls are 67% of
subagent spend and 39 grew past 200 K, so past a threshold a growing context costs more
than a fresh agent. This module owns the ADVICE ddflow gives, not the rotation -- like the
compaction advisor, ddflow never stops or restarts an agent on its own (it cannot; the
harness and the operator can). The lead reads the hint from `ddflow doctor` / `ddflow
health` and the wake brief, and decides.

The hint is a pure function of a usage number and the config: ddflow has no live context
feed yet (the transcript reader is B-am-ingest, the per-lane ledger is B-gap-spend), so
`hint` and `doctor_notes` take the agent's own last-known usage and stay SILENT when it is
unknown -- a number ddflow guessed is worse than no number, and "unknown" is the honest
answer. The window knobs (`claude_autocompact_window`, `claude_autocompact_pct_override`)
name the `CLAUDE_CODE_AUTO_COMPACT_WINDOW` / `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` alternative
a running session can still honour.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config

#: The Claude Code environment the window knobs map to (docs:
#: docs/ddflow/research/compact-window-per-agent.md, verified 2026-10-23).
AUTO_COMPACT_WINDOW_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
AUTO_COMPACT_PCT_ENV = "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"

#: The hand-off note this lane's next agent starts from: bounded, ids and pointers only.
HANDOFF_NOTE_CHARS = 400


@dataclass(frozen=True)
class Rotation:
    """What the hint says, when it fires: the number, the threshold, and the action."""

    tokens: int
    threshold: int
    text: str


def rotating(tokens_used: int | None, cfg: Config) -> bool:
    """Whether ``tokens_used`` (a context estimate in tokens, or ``None`` when unknown) is
    past ``[session].agent_rotate_tokens``. ``None`` is not past anything: DDflow does not
    guess a context it has not measured."""
    threshold = cfg.session.agent_rotate_tokens
    return threshold > 0 and tokens_used is not None and tokens_used >= threshold


def hint(tokens_used: int | None, cfg: Config) -> str:
    """The rotate hint for an agent whose context is ``tokens_used`` tokens, or ``""``.

    Silent when the feature is off (threshold 0) or the usage is unknown (``None``): a
    hint with no measured number would fire on every session. The text names the action
    the lead takes (finish, hand-off note, stop; fresh agent from `ddflow brief`) and the
    auto-compact window alternative, so the lead reads ONE decision from it."""
    if not rotating(tokens_used, cfg):
        return ""
    return (
        f"context {tokens_used} tokens, past [session].agent_rotate_tokens "
        f"({cfg.session.agent_rotate_tokens}): rotate instead of growing. Finish the item, "
        f"write a hand-off note into the item's evidence, stop; the lead starts a fresh "
        f"agent whose first call is `ddflow brief`. Auto-compact window alternative "
        f"(`CLAUDE_CODE_AUTO_COMPACT_WINDOW` / `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`): "
        f"`session.claude_autocompact_window` {cfg.session.claude_autocompact_window}, "
        f"`session.claude_autocompact_pct_override` {cfg.session.claude_autocompact_pct_override}."
    )


def handoff_note(item_id: str, summary: str) -> str:
    """The hand-off note the rotated agent writes into the item's evidence: the item id, a
    one-line summary, and the next step. The WHOLE note is capped at ``HANDOFF_NOTE_CHARS``
    so it round-trips through `ddflow brief` (the lead pastes it to the fresh agent) without
    dragging the whole transcript along."""
    suffix = "; resume with `ddflow brief`."
    prefix = f"rotated: {item_id} — "
    body = " ".join(summary.split()) or "(no summary)"
    note = prefix + body + suffix
    if len(note) > HANDOFF_NOTE_CHARS:
        room = max(HANDOFF_NOTE_CHARS - len(prefix) - len(suffix), 0)
        body = body[: max(room - 1, 0)] + "…"
        note = prefix + body + suffix
    return note[:HANDOFF_NOTE_CHARS]


def window_env(cfg: Config) -> dict[str, str]:
    """The window knobs as Claude Code environment, for the value a LAUNCH may put in the
    harness's env block (DDflow never flips a running harness's own environment). A knob at
    its 0 default is absent: leaving it out keeps the harness default, which is what 0 means."""
    out: dict[str, str] = {}
    if cfg.session.claude_autocompact_window > 0:
        out[AUTO_COMPACT_WINDOW_ENV] = str(cfg.session.claude_autocompact_window)
    if cfg.session.claude_autocompact_pct_override > 0:
        out[AUTO_COMPACT_PCT_ENV] = str(cfg.session.claude_autocompact_pct_override)
    return out


def doctor_notes(cfg: Config, context_tokens: int | None = None) -> list[str]:
    """Notes for `ddflow doctor` / `ddflow health`: the rotate hint when a measured usage is
    past the threshold (``None``/unknown is silent -- DDflow does not guess a context), and a
    note naming the auto-compact window when the operator chose it (the alternative to
    rotation). The all-default state stays silent so the every-run report is not spammed."""
    if rotating(context_tokens, cfg):
        return [hint(context_tokens, cfg)]
    window = cfg.session.claude_autocompact_window
    pct = cfg.session.claude_autocompact_pct_override
    knobs = []
    if window > 0:
        knobs.append(f"{AUTO_COMPACT_WINDOW_ENV}={window}")
    if pct > 0:
        knobs.append(f"{AUTO_COMPACT_PCT_ENV}={pct}")
    if knobs:
        return [
            f"Claude Code auto-compact window set ({', '.join(knobs)}), the alternative to "
            f"lane rotation at [session].agent_rotate_tokens={cfg.session.agent_rotate_tokens}"
        ]
    return []


def brief_hint(cfg: Config, context_tokens: int | None = None) -> str:
    """The one line the wake brief carries (the same ``hint`` the doctor notes use)."""
    return hint(context_tokens, cfg)
