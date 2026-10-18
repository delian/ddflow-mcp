"""Hook config writers by STYLE, chosen by the descriptor, not by agent name.

An agent's hooks are configured one of a few ways, and the descriptor's ``[hooks]`` says
which: ``style`` picks the writer, ``file`` is where it writes, ``events`` maps the
canonical events to the agent's own names. A new agent that speaks a style already here is
data, not code.

* ``json-groups`` -- the ``{"hooks": {Event: [{matcher?, hooks: [{type, command}]}]}}``
  layout of Claude Code and Gemini CLI (the descriptor styles ``claude`` and ``gemini``,
  which also describe the same layout in Codex, Qwen, Devin, VS Code, Goose, OpenHands and
  Tabnine); ``claudehooks`` reads and writes it and preserves every foreign hook.
* every other style (copilot, cursor, cline, cascade, antigravity, plugin) has no writer
  yet: asking for one is a REFUSAL that names the style, never a silent no-op, because a
  hook file in the wrong layout is valid JSON the agent ignores. ``none`` has nothing to
  install.

The hooks written for an agent are the ones ddflow acts on (``INSTALLED``); the table
rows ``claudehooks.HOOKS`` already carry for Claude and Gemini are used as they are.
"""

from __future__ import annotations

from pathlib import Path

from .. import claudehooks as CH
from ..harnessreg import Harness

#: Descriptor styles written as ``json-groups``.
JSON_GROUPS = frozenset({"claude", "gemini"})
#: The canonical events ddflow installs a hook for; the others have no work to do yet.
INSTALLED = ("session_start", "prompt", "pre_compact")


def unsupported(h: Harness) -> str:
    """Why ddflow cannot write ``h``'s hooks, or "" when it can."""
    hk = h.hooks
    if hk is None:
        return f"{h.id}: hooks are NOT VERIFIED in its descriptor"
    if hk.style == "none":
        return f"{h.id} has no hooks to install"
    if hk.style not in JSON_GROUPS:
        return f"{h.id}: no writer for the {hk.style!r} hook style yet"
    if not hk.file.endswith(".json") or hk.file.startswith("~"):
        return f"{h.id}: its hook file {hk.file} is not a project JSON file ddflow writes"
    if not [e for e in INSTALLED if e in hk.events]:
        return f"{h.id}: its descriptor maps none of {', '.join(INSTALLED)}"
    return ""


def specs(h: Harness) -> tuple[CH.HookSpec, ...]:
    """The hooks ddflow owns for ``h``: the table's rows when it has them, else one per
    installed event, generated from the descriptor."""
    if table := tuple(s for s in CH.HOOKS if s.agent == h.id):
        return table
    if unsupported(h):
        return ()
    hk = h.hooks
    assert hk is not None
    return tuple(
        CH.HookSpec(
            h.id,
            f"{event.replace('_', '-')}-{h.id}",
            hk.events[event],
            hk.file,
            None,
            f"the {event.replace('_', ' ')} event reaches ddflow",
            run=f"run {event} --harness {h.id}",
        )
        for event in INSTALLED
        if event in hk.events
    )


def apply(repo: Path, h: Harness, action: str) -> list[str]:
    """Install or remove every hook of ``h``. Raises CH.SettingsError / CH.NewerSettings
    for a file that must not be touched; a harness with no writer raises ``Unsupported``."""
    rows = specs(h)
    if not rows:
        raise Unsupported(unsupported(h) or f"{h.id}: no hooks to write")
    return [(CH.install_spec if action == "install" else CH.uninstall_spec)(repo, s) for s in rows]


def state(repo: Path, h: Harness) -> list[tuple[CH.HookSpec, bool | None, str]]:
    """Each owned hook of ``h`` with its (installed?, why) from the file on disk."""
    return [(s, *CH.state_spec(repo, s)) for s in specs(h)]


class Unsupported(ValueError):
    """The harness's hook style has no writer; nothing was changed."""
