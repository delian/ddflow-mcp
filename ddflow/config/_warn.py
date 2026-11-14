"""One warning per (root, key), not one per load."""

from __future__ import annotations

import sys
from pathlib import Path

from ..config_sections import _compat as CV
from ._tree import _CODE_TREE

#: (root, key) already warned about in this process. `Config.load` runs several times per
#: command -- the CLI context, the store, the hook's checks -- and the same warning five
#: times over reads as five problems.
_WARNED: set[tuple[str, str]] = set()

#: (root, note) already warned about as a lower layer dropping a higher layer's list
#: entries. Keyed by the WHOLE note (not the key alone): the same knob's dropped set can
#: change across reloads in one process, and each new drop must be said, never reused
#: from the earlier note.
_WARNED_DROPPED: set[tuple[str, str]] = set()


def _say(message: str) -> None:
    """One stderr line, prefixed. The module's single `print` site, shared by
    `_warn_unknown` and `_warn_dropped`, so the `t201` (print-outside-surfaces) ratchet
    keeps counting ONE warn-print while both warnings print. stderr, never stdout, so
    `--json` output and MCP replies stay parseable."""
    print(f"ddflow: warning: {message}", file=sys.stderr)


def _warn_unknown(
    keys: list[str],
    root: Path,
    fallbacks: set[str] | frozenset[str] = frozenset(),
    ignored: list[str] | None = None,
) -> None:
    """Say, on stderr, which config keys this code skipped, and which unknown enum values
    it replaced (each note names the value in effect: the strictest, or a later layer's),
    and which members of a checked list it ignored (``ignored``).

    Skipping without a word is the silent-knob-drop class: 81a52e3 made an unknown key
    load-and-skip so an older tree keeps working, but only `doctor` mentioned it, so
    every other command ran with the knob dropped and nobody was told.
    """
    new = [k for k in [*keys, *(ignored or [])] if (str(root), k) not in _WARNED]
    if not new:
        return
    _WARNED.update((str(root), k) for k in new)
    # An enum knob's unknown value is not skipped: it is APPLIED as the knob's strictest
    # value (D-enum-fallback-strict), and saying "skipped" would read as "no effect".
    fell_back = [k for k in new if k in fallbacks]
    members = [k for k in new if k in (ignored or [])]
    skipped = [k for k in new if k not in fallbacks and k not in members]
    what = []
    if skipped:
        what.append(f"{', '.join(skipped)}, which this ddflow does not know; skipped")
    if fell_back:
        what.append(f"{', '.join(fell_back)}; each such knob takes the value its note names")
    if members:
        what.append(f"{'; '.join(members)}; the rest of each is applied")
    # How to catch up depends on how this ddflow is installed: a checkout merges main.
    fix = "merge main into this tree" if CV.is_source_tree() else CV.upgrade_advice(kind="index")
    _say(
        f"{root / '.ddflow'}/config.toml or local/config.toml sets "
        f"{'. It sets '.join(what)}. (This ddflow: {_CODE_TREE}.) The config is newer "
        f"than this code: {fix} (or, if it is a typo, fix it; `ddflow doctor` lists each)."
    )


def _warn_dropped(notes: list[str], root: Path) -> None:
    """Say, on stderr, which list-knob entries a later config LAYER dropped by replacing a
    list an earlier layer had set (D-config-layer-order: lists replace). r-tighten-only: a
    lower layer may tighten, never SILENTLY loosen, so the dropped entries are named at
    load (and listed by `doctor`), never gone in silence.
    """
    new = [n for n in notes if (str(root), n) not in _WARNED_DROPPED]
    if not new:
        return
    _WARNED_DROPPED.update((str(root), n) for n in new)
    _say(
        f"a lower config layer turns a higher layer's list entries off: {'; '.join(new)}. "
        "A later layer's list replaces the earlier one's (D-config-layer-order); every entry "
        "dropped is named here (by the layer that did it), never silent (r-tighten-only)."
    )
