"""The wiring files a feature item must declare: what a NEW command, knob or event kind
touches beside the file that declares it (B-item-globs-omit-wiring-files).

A feature item under-declares its globs. It names the file it CREATES -- a new
`config_sections/<section>.py`, a new `templates/events/<owner>.toml`, a new
`surfaces/declared/<name>.py` -- but not the shared files that WIRE that thing into the
rest of ddflow: the config registry and default table a knob mirrors, the event-template
directory, the command/parser surfaces, the api layer and the MCP tool registry.
`enforce.commit_without_lease` then refuses the first write to those wiring files, so the
change a feature item exists to make is blocked at the first commit.

This module is the ONE declaration of that wiring. `task add` (and the bug auto-filer,
which files through the same `services.items.add_task`) expands an item's globs from a
declaration of WHAT the item adds instead of every caller guessing the same list, and
keeps the list in one map so a new wiring file surfaces here rather than as another
under-declared feature item.

Paths are repo-root-relative globs, exactly as `task add --globs` takes them. The map is
keyed by the feature class a `task add --adds knob|event|command` names; ``expand`` is the
whole behaviour, pure over a string list, so it is cheap to pin and cheap to reuse
without importing the state or the log.
"""

from __future__ import annotations

from collections.abc import Iterable

#: The feature classes an item can declare it adds; the keys of `WIRING`.
FEATURE_CLASSES = ("knob", "event", "command")

#: feature class -> the wiring globs a new one of that class must declare, in order.
#
#: knob ->    the config-section file its knobs are declared in (one dataclass per
#:            ``ddflow/config_sections/*.py``, discovered by scan) plus the shipped default
#:            table ``templates/default.toml`` every knob's default mirrors.
#: event ->   the event-kind template ``templates/events/*.toml`` its kinds are declared in.
#: command -> the declared/command/parser surfaces a command is wired through, the api layer
#:            ``ddflow/api/__init__.py`` that re-exports it, and the MCP tool registry
#:            ``surfaces/tools/*`` that places its tool (families, tiers, order, aliases).
WIRING: dict[str, tuple[str, ...]] = {
    "knob": ("ddflow/config_sections/*.py", "ddflow/templates/default.toml"),
    "event": ("ddflow/templates/events/*.toml",),
    "command": (
        "ddflow/surfaces/declared/*",
        "ddflow/surfaces/commands/*",
        "ddflow/surfaces/parsers/*",
        "ddflow/api/__init__.py",
        "ddflow/surfaces/tools/*",
    ),
}


def expand(globs: Iterable[str] | None, classes: Iterable[str] | None) -> list[str]:
    """``globs`` with the wiring globs of every feature class ``classes`` names, appended
    in order without repeats (the item's own globs keep their order first; a glob it
    already declared is not repeated)."""
    out = list(globs or [])
    for cls in classes or []:
        for g in WIRING.get(cls, ()):
            if g not in out:
                out.append(g)
    return out


def problem(classes: Iterable[str] | None) -> str:
    """Why ``classes`` cannot be a feature-class declaration, or ""."""
    bad = sorted({c for c in (classes or []) if c not in WIRING})
    if not bad:
        return ""
    return (
        f"unknown feature class(es) {', '.join(repr(b) for b in bad)}: a task adds a "
        f"{', '.join(FEATURE_CLASSES)}."
    )
