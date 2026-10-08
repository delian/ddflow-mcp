"""ChangeDetector: what changed, by content (D-unify: one local background layer).

Five digest-drift detectors and four co-change checks are planned (documentation watch,
stale rules, skill and subagent sync, template refresh, ...). Each asks the same question
of a set of files -- "which of them differ from the last time I looked?" -- so the
question is answered once here, and each of them registers a glob set rather than
building a scanner.

Two answers, for two situations:

- `ChangeDetector.scan` / `.changes`: the files matching a glob set on disk, as a
  ``path -> content hash`` manifest, and what differs between a stored manifest and the
  disk now (added, modified, removed). A file that is there but cannot be read is neither
  removed nor modified: it keeps the hash it last had and is listed in `unreadable`. The manifest is plain data the caller keeps
  wherever it keeps state; nothing here writes. A file whose size and modification time
  (and change time) are unchanged since the detector last hashed it is not read again, so a
  scan that finds nothing new costs one ``stat`` per file -- except for a file modified too
  recently to trust: a coarse clock can give two quick writes one timestamp, so a file whose
  mtime is within `RACY_NS` of the moment it was hashed is hashed again (git's "racily
  clean" rule). The change time is part of the signature because a tool that restores
  mtimes (``cp -p``, ``tar -x``, ``git checkout``) cannot restore it.
- `changed_since`: the paths git says differ from a commit -- committed after it, staged,
  unstaged and untracked -- optionally narrowed to a glob set. None when git cannot say:
  "could not tell" is never "nothing changed".

Paths are POSIX strings relative to the root, spelt as the filesystem spells them. A glob
has `core.globs` meaning (git's ``:(glob)`` pathspec), so a path this module calls
matching is one a claim or a docs allowlist would call matching.
"""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..core import digest, globs
from ..infra import git as G

#: Names no scan descends into or lists: git's own (a directory, or the `.git` FILE of a linked
#: worktree or submodule), never part of a work tree's content.
PRUNE = (".git",)
#: A file modified less than this long before it was hashed may be rewritten within the same
#: timestamp tick (FAT keeps two seconds): its cached hash is not trusted.
RACY_NS = 2_000_000_000
#: Bytes read per ``read`` while hashing a file.
CHUNK = 1 << 20

Manifest = dict[str, str]


@dataclass(frozen=True)
class Changes:
    """The difference between two manifests: sorted paths."""

    added: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.added or self.modified or self.removed)

    @property
    def paths(self) -> tuple[str, ...]:
        """Every changed path, sorted, each once."""
        return tuple(sorted({*self.added, *self.modified, *self.removed}))


def matches(path: str, patterns: Iterable[str]) -> bool:
    """Is ``path`` matched by any of ``patterns``? An empty set matches everything."""
    pats = list(patterns)
    return not pats or any(globs.match(path, g) for g in pats)


def diff(old: Manifest, new: Manifest) -> Changes:
    """What differs between two manifests."""
    return Changes(
        added=tuple(sorted(p for p in new if p not in old)),
        modified=tuple(sorted(p for p in new if p in old and new[p] != old[p])),
        removed=tuple(sorted(p for p in old if p not in new)),
    )


def file_hash(path: Path) -> str | None:
    """The content hash of a regular file; None when it cannot be read (vanished, denied)."""
    h = digest.hasher()
    try:
        with path.open("rb") as fh:
            while block := fh.read(CHUNK):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


@dataclass
class ChangeDetector:
    """The files under ``root`` matching ``patterns`` (all of them when empty), minus
    ``exclude``, as a manifest; and what changed against an earlier one.

    ``hasher`` maps a path to its content hash (None: unreadable) and is injectable, so a
    test counts the reads and a caller can hash something other than file bytes.
    """

    root: Path
    patterns: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    prune: tuple[str, ...] = PRUNE
    hasher: Callable[[Path], str | None] = file_hash
    #: Nanoseconds now, on the clock the file times use; injectable for a test.
    now_ns: Callable[[], int] = time.time_ns
    #: path -> ((size, mtime_ns, ctime_ns), hash) of the last trusted read: the unread-again cache.
    _seen: dict[str, tuple[tuple[int, int, int], str]] = field(default_factory=dict, repr=False)
    #: Files the last `scan` found but could not read.
    unreadable: list[str] = field(default_factory=list, repr=False)
    #: path -> the last hash taken, trusted or not: what an unreadable file keeps.
    _last: Manifest = field(default_factory=dict, repr=False)

    def candidates(self) -> list[str]:
        """The matching files on disk, directory by directory in name order. Symlinks to
        directories are not followed; `scan` leaves out whatever is not a regular file."""
        out: list[str] = []
        root = str(self.root)
        for here, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in self.prune)
            rel_dir = os.path.relpath(here, root)
            for name in sorted(n for n in names if n not in self.prune):
                rel = name if rel_dir == "." else f"{rel_dir}/{name}".replace(os.sep, "/")
                if matches(rel, self.patterns) and not any(
                    globs.match(rel, g) for g in self.exclude
                ):
                    out.append(rel)
        return out

    def scan(self) -> Manifest:
        """The manifest of the disk now. A file that vanishes or cannot be read while
        scanning is left out, as if it were not there."""
        manifest: Manifest = {}
        live: set[str] = set()
        self.unreadable = []
        for rel in self.candidates():
            path = self.root / rel
            try:
                st = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            sig = (st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            cached = self._seen.get(rel)
            if cached is not None and cached[0] == sig:
                value: str | None = cached[1]
            else:
                started = self.now_ns()
                value = self.hasher(path)
                self._seen.pop(rel, None)
                if value is not None and started - st.st_mtime_ns >= RACY_NS:
                    self._seen[rel] = (sig, value)
            if value is None:
                self.unreadable.append(rel)
                value = self._last.get(rel)
            if value is not None:
                manifest[rel] = value
                self._last[rel] = value
            live.add(rel)
        for gone in set(self._seen) - live:
            del self._seen[gone]
        for gone in set(self._last) - live:
            del self._last[gone]
        return manifest

    def changes(self, old: Manifest) -> tuple[Changes, Manifest]:
        """(what differs from ``old``, the manifest of now): keep the second for the next call."""
        now = self.scan()
        return diff(old, now), now


def changed_since(
    root: Path, sha: str, patterns: Iterable[str] = (), exclude: Iterable[str] = ()
) -> list[str] | None:
    """Every path that differs from commit ``sha`` in ``root``: committed after it, staged,
    unstaged and untracked (not ignored); a rename counts as BOTH its paths. Narrowed to
    ``patterns`` (all when empty) minus ``exclude``. Sorted. None when git could not say."""
    listings = [
        G.git_paths(root, "diff", "--name-only", "--no-renames", sha),
        G.git_paths(root, "ls-files", "--others", "--exclude-standard"),
    ]
    if any(listing is None for listing in listings):
        return None
    pats, skip = list(patterns), list(exclude)
    found = {p for listing in listings for p in listing or ()}
    return sorted(p for p in found if matches(p, pats) and not any(globs.match(p, g) for g in skip))
