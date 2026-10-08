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

- `changed_paths` / `base_changed`: what differs from a base -- committed, staged, tracked,
  untracked -- as the ONE answer every "changed against the base" caller reads
  (decision D-unify). A rename counts as both its paths unless the caller asks for git's
  own rename detection. None / None when git cannot say: "could not tell" is never
  "nothing changed".

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

    def scan(self, known: Manifest | None = None) -> Manifest:
        """The manifest of the disk now. A file that vanishes while scanning is left out. One
        that cannot be read is listed in `unreadable` and keeps its hash in ``known`` (the
        manifest the caller holds), else it is left out: it is never reported as changed
        on the strength of an error."""
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
                value = (known or {}).get(rel)
            if value is not None:
                manifest[rel] = value
            live.add(rel)
        for gone in set(self._seen) - live:
            del self._seen[gone]
        return manifest

    def changes(self, old: Manifest) -> tuple[Changes, Manifest]:
        """(what differs from ``old``, the manifest of now): keep the second for the next call."""
        now = self.scan(old)
        return diff(old, now), now


def changed_since(
    root: Path, sha: str, patterns: Iterable[str] = (), exclude: Iterable[str] = ()
) -> list[str] | None:
    """Every path that differs from commit ``sha`` in ``root``: committed after it, staged,
    unstaged and untracked (not ignored); a rename counts as BOTH its paths. Narrowed to
    ``patterns`` (all when empty) minus ``exclude``. Sorted. None when git could not say."""
    listings = [
        G.git_paths(root, "diff", "--name-only", "--no-renames", sha),
        # The index alone: a change staged and then undone in the work tree, or a path
        # removed from the index but still on disk, differs from `sha` only here.
        G.git_paths(root, "diff", "--cached", "--name-only", "--no-renames", sha),
        G.git_paths(root, "ls-files", "--others", "--exclude-standard"),
    ]
    if any(listing is None for listing in listings):
        return None
    pats, skip = list(patterns), list(exclude)
    found = {p for listing in listings for p in listing or ()}
    return sorted(p for p in found if matches(p, pats) and not any(globs.match(p, g) for g in skip))


#: The four places a change can be, in the order `changed_paths` reads them.
KINDS = ("committed", "staged", "tracked", "untracked")


def changed_paths(
    root: Path | str,
    base: str = "",
    *,
    tip: str = "HEAD",
    include: Iterable[str] | None = None,
    renames: bool = False,
    fork: bool = True,
    pathspec: Iterable[str] = (),
    literal: bool = False,
    diff_filter: str = "",
) -> list[str] | None:
    """The paths that differ from ``base``, sorted, or None when git could not say.

    ``include`` picks from `KINDS`: ``committed`` (``base`` to ``tip``: with ``fork``, what
    ``tip`` did since it left ``base``, git's ``base...tip``; without, the two commits
    compared as they are), ``staged`` (the index against HEAD), ``tracked`` (tracked
    files, staged or not, against HEAD) and ``untracked`` (not ignored). The default is
    every kind that applies: ``committed`` only when a ``base`` is given, and the three
    tree kinds only when ``tip`` is HEAD. A rename is both its old and its new path
    unless ``renames``. ``pathspec`` narrows (``literal``: names, not globs);
    ``diff_filter`` is git's ``--diff-filter``.
    """
    kinds = _kinds(include, base, tip)
    spec = list(pathspec)
    tail = ["--", *spec] if spec else []
    pre = ["--literal-pathspecs"] if literal else []
    flags = ["--name-only"] if renames else ["--name-only", "--no-renames"]
    if diff_filter:
        flags.append(f"--diff-filter={diff_filter}")
    diff = ("diff", *flags)
    queries: dict[str, Callable[[], list[str] | None]] = {
        "committed": lambda: G.paths(
            root, *pre, *diff, *([f"{base}...{tip}"] if fork else [base, tip]), *tail
        ),
        "staged": lambda: G.paths(root, *pre, "diff", "--cached", *flags, *tail),
        "tracked": lambda: G.paths(root, *pre, *diff, "HEAD", *tail),
        "untracked": lambda: G.files(root, "untracked", pathspec=tuple(spec), literal=literal),
    }
    found = [queries[k]() for k in kinds]
    if any(q is None for q in found):
        return None
    return sorted({p for q in found for p in q or ()})


def _kinds(include: Iterable[str] | None, base: str, tip: str) -> tuple[str, ...]:
    """The kinds of change `changed_paths` reads: ``include``, or by default every kind
    that applies (`committed` needs a base; the tree kinds need ``tip`` to be HEAD)."""
    if include is None:
        kinds = tuple(
            k for k in KINDS if (base or k != "committed") and (tip == "HEAD" or k == "committed")
        )
        if not kinds:
            raise ValueError("no kind of change applies: pass a base, or leave tip at HEAD")
        return kinds
    kinds = tuple(include)
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise ValueError(f"unknown kind(s) of change {unknown!r}; expected from {KINDS}")
    if "committed" in kinds and not base:
        raise ValueError("a committed change is against a base: pass base")
    if not kinds:
        raise ValueError("include names no kind of change")
    return kinds


def base_changed(root: Path | str, base: str, **kw) -> bool | None:
    """Does the tree differ from ``base`` at all? None when git could not say -- never
    False for a git failure. ``kw`` is `changed_paths`'."""
    found = changed_paths(root, base, **kw)
    return None if found is None else bool(found)
