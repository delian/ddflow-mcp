"""Local backups: the originals of files a ddflow command is about to rewrite.

Decision D-upgrade-backups: copies go to `.ddflow/backups/<stamp>-<label>/` (local, git-ignored,
never shared), each file at its project-relative path -- one outside the project under
`_outside/` -- with a `manifest.json` saying what was there. Used by `ddflow upgrade --apply`
(`services.upgrade_apply`) and by `ddflow adopt --refresh-docs` (`services.adopt`).
"""

from __future__ import annotations

import json
import shutil
import stat
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import clock
from ..infra import git
from ..infra.fsio import atomic_write, ensure_ignored_dir, replace_text

BACKUPS = ".ddflow/backups"
MANIFEST = "manifest.json"
#: The copies live under this directory, so a project file named like the manifest cannot
#: overwrite it.
FILES = "files"
INSIDE = "in"  #: files of the project, at their project-relative path
OUTSIDE = "out"  #: files elsewhere (a git hooks directory), at their absolute path


def backup_name(frm: str, to: str) -> str:
    """`<stamp>-<from>-to-<to>`: the directory an upgrade from ``frm`` to ``to`` backs up into."""
    return f"{clock.compact_at()}-{frm or 'unstamped'}-to-{to}"


def make_backup(repo: Path, files: Collection[Path], frm: str, to: str, *, name: str = "") -> Path:
    """Copy every file that exists to `.ddflow/backups/<stamp>-<from>-to-<to>/` (or
    ``name``), a file inside the project at its relative path and one outside it under
    `out/` (both below `files/`: `files/in/...`, `files/out/...`), and write a `manifest.json` listing each file, whether it existed and where
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
    else:
        raise OSError(f"{root}: too many backups named {name}")
    entries: list[dict[str, Any]] = []
    for f in dict.fromkeys(Path(x).resolve() for x in files):
        try:
            shown = f.relative_to(repo).as_posix()
            stored = f"{INSIDE}/{shown}"
        except ValueError:
            shown = f.as_posix()
            stored = f"{OUTSIDE}/{shown.lstrip('/')}"
        existed = f.is_file()
        if existed:
            target = dest / FILES / stored
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
        entries.append({"path": shown, "stored": stored if existed else "", "existed": existed})
    manifest = {"from": frm, "to": to, "mode": "local", "files": entries}
    replace_text(dest / MANIFEST, json.dumps(manifest, indent=2) + "\n")
    return dest


def prune(repo: Path, keep: int) -> list[str]:
    """Remove the oldest backups beyond ``keep`` (0 keeps every one); the names removed.

    Only directories this module wrote (each holds a `manifest.json`) are touched, and the
    newest ``keep`` stay: names start with a sortable UTC stamp."""
    root = Path(repo) / BACKUPS
    if keep <= 0 or not root.is_dir():
        return []
    try:
        ours = sorted(p for p in root.iterdir() if p.is_dir() and (p / MANIFEST).is_file())
    except OSError:  # unreadable or gone meanwhile: nothing to prune, and nothing to fail over
        return []
    removed: list[str] = []
    for old in ours[: max(0, len(ours) - keep)]:
        shutil.rmtree(old, ignore_errors=True)
        if not old.exists():  # a removal that was refused is not a removal
            removed.append(old.name)
    return removed


# -- snapshot: a git-tracked backup (decision D-upgrade-backups) ---------------------------------

#: Tags a snapshot makes: `ddflow-upgrade-snapshot/<stamp>-<from>-to-<to>`.
SNAPSHOT_PREFIX = "ddflow-upgrade-snapshot/"


class SnapshotRefused(RuntimeError):
    """A snapshot cannot be taken (no git, a dirty tree): said plainly, nothing was changed."""


@dataclass(frozen=True)
class Snapshot:
    #: The tag that names it; `git checkout <tag> -- <file>` or `ddflow upgrade --restore` undo it.
    tag: str
    #: The commit it holds (HEAD, or the commit that added files git did not track yet).
    commit: str
    #: Project paths the snapshot holds (they existed), and those that did not exist before.
    held: tuple[str, ...]
    created: tuple[str, ...]
    #: Where files git cannot hold (ignored, or outside the project: git hooks) were copied.
    local: str = ""


def _in_git(repo: Path, rel: str) -> bool:
    """Can git hold ``rel``: it is not ignored?"""
    return not git.run(repo, "check-ignore", "-q", "--", rel).ok


def make_snapshot(repo: Path, files: Collection[Path], frm: str, to: str) -> Snapshot:
    """Tag the state before an upgrade, so a revert or `ddflow upgrade --restore` undoes it.

    Refused (`SnapshotRefused`) without git or on a tree with uncommitted changes to tracked
    files: the snapshot is what HEAD holds, and a dirty tree would not be in it. Affected files
    git does not track yet are committed first ("ddflow: snapshot before upgrade"); files it
    cannot hold -- ignored ones, and the git hooks, which live outside the tree -- go to a
    local backup beside it, named in `Snapshot.local`."""
    repo = Path(repo).resolve()
    if not git.run(repo, "rev-parse", "--is-inside-work-tree").ok:
        raise SnapshotRefused(
            "a snapshot backup needs a git repository, and this is not one; "
            "use [upgrade].backup = local (or --backup local)"
        )
    dirty = git.run(repo, "status", "--porcelain", "--untracked-files=no")
    if not dirty.ok or dirty.out:
        raise SnapshotRefused(
            "a snapshot backup needs a clean working tree (it is what HEAD holds), and this "
            "one has uncommitted changes to tracked files: commit or stash them, or use "
            "[upgrade].backup = local (or --backup local)"
        )
    held: list[str] = []
    created: list[str] = []
    untracked: list[str] = []
    elsewhere: list[Path] = []
    for f in dict.fromkeys(Path(x).resolve() for x in files):
        try:
            rel = f.relative_to(repo).as_posix()
        except ValueError:
            elsewhere.append(f)
            continue
        if not f.is_file():
            created.append(rel)
        elif rel.startswith(".git/") or not _in_git(repo, rel):
            elsewhere.append(f)
        else:
            held.append(rel)
            if not git.run(repo, "ls-files", "--error-unmatch", "--", rel).ok:
                untracked.append(rel)
    if untracked:
        add = git.run(repo, "add", "--", *untracked)
        done = git.run(
            repo, "commit", "-q", "-m", "ddflow: snapshot before upgrade", "--", *untracked
        )
        if not (add.ok and done.ok):
            raise SnapshotRefused(f"git could not commit the snapshot: {(done.err or add.err)}")
    head = git.run(repo, "rev-parse", "HEAD")
    if not head.ok:
        raise SnapshotRefused(
            "a snapshot backup needs a commit to point at (the repository has none yet)"
        )
    tag = f"{SNAPSHOT_PREFIX}{backup_name(frm, to)}"
    note = json.dumps({"held": held, "created": created}, indent=1)
    made = git.run(repo, "tag", "-a", tag, "-m", note, head.out)
    if not made.ok:
        raise SnapshotRefused(f"git could not tag the snapshot: {made.text()}")
    local = str(make_backup(repo, elsewhere, frm, to)) if elsewhere else ""
    return Snapshot(tag, head.out, tuple(held), tuple(created), local)


def snapshots(repo: Path) -> list[str]:
    """The snapshot tags of this repository, oldest first (their names sort by time)."""
    out = git.run(Path(repo), "tag", "--list", f"{SNAPSHOT_PREFIX}*")
    return sorted(out.out.splitlines()) if out.ok else []


def local_backups(repo: Path) -> list[str]:
    root = Path(repo) / BACKUPS
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / MANIFEST).is_file())


def _stamp(name: str) -> str:
    return name.removeprefix(SNAPSHOT_PREFIX)


def newest(repo: Path) -> str:
    """The most recent backup or snapshot, "" when there is none."""
    names = [*local_backups(repo), *snapshots(repo)]
    return max(names, key=_stamp) if names else ""


def restore(repo: Path, name: str) -> dict[str, Any]:
    """Put back what a backup or a snapshot holds: ``name`` is a directory under
    `.ddflow/backups/`, a snapshot tag, or ``latest``. What is there now is saved first
    (a local backup named ``<stamp>-restore-of-<name>``), so a restore can be undone too.
    Returns ``{"name", "kind", "restored", "removed", "saved"}``; raises ``LookupError`` for a
    name that is neither, ``SnapshotRefused`` for git failing."""
    repo = Path(repo).resolve()
    if name in ("", "latest"):
        name = newest(repo)
        if not name:
            raise LookupError("there is no upgrade backup or snapshot to restore")
    if name.startswith(SNAPSHOT_PREFIX):
        return _restore_snapshot(repo, name)
    directory = repo / BACKUPS / name
    if not (directory / MANIFEST).is_file():
        known = ", ".join([*local_backups(repo), *snapshots(repo)]) or "none"
        raise LookupError(f"no upgrade backup or snapshot named {name!r}; known: {known}")
    return _restore_local(repo, directory)


def _abs(repo: Path, shown: str) -> Path:
    p = Path(shown)
    return p if p.is_absolute() else repo / p


def _restore_local(repo: Path, directory: Path) -> dict[str, Any]:
    manifest = json.loads((directory / MANIFEST).read_text("utf-8"))
    entries = manifest.get("files", [])
    targets = [_abs(repo, e["path"]) for e in entries]
    saved = make_backup(
        repo, targets, "", "", name=f"{clock.compact_at()}-restore-of-{directory.name}"
    )
    restored: list[str] = []
    removed: list[str] = []
    for e in entries:
        target = _abs(repo, e["path"])
        if e.get("existed") and e.get("stored"):
            copy = directory / FILES / e["stored"]
            atomic_write(target, copy.read_bytes(), mode=stat.S_IMODE(copy.stat().st_mode))
            restored.append(e["path"])
        elif target.exists():  # the upgrade created it: before it, there was nothing
            target.unlink()
            removed.append(e["path"])
    return {
        "name": directory.name,
        "kind": "backup",
        "restored": restored,
        "removed": removed,
        "saved": str(saved),
    }


def _restore_snapshot(repo: Path, tag: str) -> dict[str, Any]:
    shown = git.run(repo, "tag", "-l", "--format=%(contents)", tag)
    if not shown.ok or not shown.out:
        raise LookupError(f"no snapshot tag {tag!r} in this repository")
    note = json.loads(shown.out)
    held, created = list(note.get("held", [])), list(note.get("created", []))
    saved = make_backup(
        repo,
        [repo / p for p in (*held, *created)],
        "",
        "",
        name=f"{clock.compact_at()}-restore-of-{tag.removeprefix(SNAPSHOT_PREFIX)}",
    )
    if held:
        out = git.run(repo, "checkout", tag, "--", *held)
        if not out.ok:
            raise SnapshotRefused(f"git could not restore the snapshot: {out.text()}")
    removed = []
    for rel in created:
        if (repo / rel).exists():
            (repo / rel).unlink()
            removed.append(rel)
    return {
        "name": tag,
        "kind": "snapshot",
        "restored": held,
        "removed": removed,
        "saved": str(saved),
    }
