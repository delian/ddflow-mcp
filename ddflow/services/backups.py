"""Local backups: the originals of files a ddflow command is about to rewrite.

Decision D-upgrade-backups: copies go to `.ddflow/backups/<stamp>-<label>/` (local, git-ignored,
never shared), each file at its project-relative path -- one outside the project under
`_outside/` -- with a `manifest.json` saying what was there. Used by `ddflow upgrade --apply`
(`services.upgrade_apply`) and by `ddflow adopt --refresh-docs` (`services.adopt`).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Collection
from pathlib import Path
from typing import Any

from ..core import clock
from ..infra.fsio import ensure_ignored_dir, replace_text

BACKUPS = ".ddflow/backups"
MANIFEST = "manifest.json"


def backup_name(frm: str, to: str) -> str:
    """`<stamp>-<from>-to-<to>`: the directory an upgrade from ``frm`` to ``to`` backs up into."""
    return f"{clock.compact_at()}-{frm or 'unstamped'}-to-{to}"


def make_backup(repo: Path, files: Collection[Path], frm: str, to: str, *, name: str = "") -> Path:
    """Copy every file that exists to `.ddflow/backups/<stamp>-<from>-to-<to>/` (or
    ``name``), a file inside the project at its relative path and one outside it under
    `_outside/`, and write a `manifest.json` listing each file, whether it existed and where
    its copy is. Returns the backup directory. Raises OSError when it cannot be written: the
    caller then writes nothing."""
    repo = Path(repo).resolve()
    root = ensure_ignored_dir(
        repo / BACKUPS, comment="ddflow backups: local, not shared, never committed"
    )
    name = name or backup_name(frm, to)
    dest = root / name
    for n in range(2, 100):  # two backups in one clock tick keep their own directories
        try:
            dest.mkdir(parents=True)
            break
        except FileExistsError:
            dest = root / f"{name}-{n}"
    entries: list[dict[str, Any]] = []
    for f in dict.fromkeys(Path(x).resolve() for x in files):
        try:
            stored = f.relative_to(repo).as_posix()
            shown = stored
        except ValueError:
            stored = "_outside/" + f.as_posix().lstrip("/")
            shown = f.as_posix()
        existed = f.is_file()
        if existed:
            target = dest / stored
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
        entries.append({"path": shown, "stored": stored if existed else "", "existed": existed})
    manifest = {"from": frm, "to": to, "mode": "local", "files": entries}
    replace_text(dest / MANIFEST, json.dumps(manifest, indent=2) + "\n")
    return dest
