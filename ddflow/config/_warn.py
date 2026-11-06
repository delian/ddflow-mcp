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
    every other command ran with the knob dropped and nobody was told. stderr, so
    `--json` output and MCP replies stay parseable.
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
    print(
        f"ddflow: warning: {root / '.ddflow'}/config.toml or local/config.toml sets "
        f"{'. It sets '.join(what)}. (This ddflow: {_CODE_TREE}.) The config is newer "
        f"than this code: {fix} (or, if it is a typo, fix it; "
        "`ddflow doctor` lists each).",
        file=sys.stderr,
    )
