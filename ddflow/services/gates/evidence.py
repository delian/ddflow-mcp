"""What a gate's evidence is ABOUT: the tree fingerprint, content ids and diff stats of a worktree."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...core.bookkeeping import STATE_EXCLUDE, is_state
from ...core.digest import content_digest
from ...infra import git as GIT

#: How many untracked files `tree_fingerprint` will hash before giving up on content
#: and falling back to their names alone. `--exclude-standard` already drops anything
#: gitignored, so a repository normally has a handful; a run that has just dumped ten
#: thousand artifacts into an un-ignored directory should not turn every gate into a
#: full read of them. Configurable because the right number depends on the project --
#: raise it if your work genuinely spans more new files than this.
MAX_UNTRACKED_HASHED = 512


#: Paths the fingerprint must IGNORE. `.ddflow/` is this tool's own bookkeeping, and
#: recording a gate's outcome writes an event into it — so including it made the
#: fingerprint move as a DIRECT RESULT of taking it, and every completion warned that
#: the tree had changed since the gate ran. The evidence a gate produces is about the
#: SOURCE; the fact that recording it appended to a log is not a reason to distrust it.
#:
#: Expressed as git pathspecs so the exclusion happens inside git rather than by
#: filtering its output afterwards — a post-filter has to re-implement pathspec
#: matching, and would drift from what git itself considers inside the directory.
#: Named once in `core.bookkeeping`.
FINGERPRINT_EXCLUDE: tuple[str, ...] = STATE_EXCLUDE

#: What a fingerprint digests for an untracked listing git could not produce: a tree whose
#: untracked files are unknown is not the tree with none (it used to read as none).
UNLISTED = "unlisted"


def _untracked_listing(cwd: Path) -> list[str] | None:
    """Untracked, non-ignored paths, excluding ddflow's own state; None when git could
    not list them. ONE spelling.

    `git ls-files --others --exclude-standard -- . *FINGERPRINT_EXCLUDE` was written out
    twice in this module and run twice per gate. Two copies of an argv list is two
    chances for one of them to forget the exclusion, which is exactly how `.ddflow/`
    crept into a fingerprint and made every completion warn that the tree had moved.
    Read with `-z`: a non-ASCII name is the file's own, not its C-quoted spelling.
    """
    return GIT.files(cwd, "untracked", exclude=FINGERPRINT_EXCLUDE)


def _untracked_paths(cwd: Path) -> list[str]:
    """`_untracked_listing`, empty when git could not list them: for the callers that only
    count (`diff_stat` reports zeros when git cannot answer). The fingerprint tells the two
    apart (`_untracked_digest`)."""
    return _untracked_listing(cwd) or []


def _untracked_digest(cwd: Path) -> str:
    """Content ids for untracked files, or their names when there are too many.

    `git hash-object` WITHOUT `-w`: it computes the object ids and writes nothing to
    the object store, so this stays an observation. Binary content is covered here for
    free, because hash-object hashes bytes and does not care what they are.
    """
    from ...infra import worktree as W

    listed = _untracked_listing(cwd)
    if listed is None:
        return UNLISTED
    paths = listed
    if not paths:
        return ""
    if len(paths) > MAX_UNTRACKED_HASHED:
        # Names only. Degraded, and SAID so in the digest rather than silently: a
        # fingerprint that quietly stopped covering content would make `stale_evidence`
        # go quiet for the repositories that need it most.
        return f"names-only:{len(paths)}:" + digest("\n".join(sorted(paths)))
    # `--`: an untracked file named `-w` is a path, never the option that WRITES the
    # object (Bc63747e0a4).
    hashed = W.git(cwd, "hash-object", "--", *paths)
    ids = hashed.out.splitlines()
    # A short or long reply must not be zipped silently: `zip` would truncate to the
    # shorter list, pairing hashes with the wrong paths and producing a fingerprint
    # that looks entirely plausible and means nothing. Fall back to names, which is
    # weaker but honest.
    if not hashed.ok or len(ids) != len(paths):
        return digest("\n".join(sorted(paths)))
    return digest("\n".join(f"{h} {p}" for h, p in zip(ids, paths, strict=True)))


#: `git diff --numstat` emits three tab-separated fields per changed file:
#: additions, deletions, path.
_NUMSTAT_FIELDS = 3


def diff_stat(cwd: Path) -> dict[str, int]:
    """Files and lines changed against HEAD, plus untracked files. Read-only.

    Deliberately NOT part of `tree_fingerprint`: a fingerprint answers "is this the
    same tree", and two different trees can share a line count. Mixing a human-readable
    magnitude into an identity would make the identity weaker and the magnitude
    unavailable on its own.

    Uses the same `.ddflow/` exclusion as the fingerprint, for the same reason: a
    number that grows every time ddflow records an event describes ddflow's bookkeeping
    rather than the work.

    Returns zeros rather than raising outside a repository. A gate can legitimately run
    where git does not reach, and a missing statistic is honest where a fabricated one
    is not -- but the keys are always present, so a reader never has to distinguish
    "no change" from "the field did not exist yet".
    """
    from ...infra import worktree as W

    out = {"files": 0, "insertions": 0, "deletions": 0, "untracked": 0}
    # Untracked FIRST, and outside the HEAD guard: it needs no commit. A repository
    # before its first commit -- `git init`, scaffold a module, run the test gate -- used
    # to report every field zero, so the one gate run where everything is new reported
    # the smallest possible change.
    untracked = _untracked_paths(cwd)
    out["untracked"] = len(untracked)
    # ...and their LINES count toward the magnitude. `git diff HEAD --numstat` never
    # reports untracked content, so a task that is entirely new files -- the ordinary
    # shape of a new module -- showed 0 insertions while `tree_fingerprint` deliberately
    # hashes exactly those bytes. The two halves of the same evidence disagreed about
    # whether the work existed.
    if len(untracked) <= MAX_UNTRACKED_HASHED:
        for rel in untracked:
            try:
                blob = (Path(cwd) / rel).read_bytes()
            except OSError:
                continue
            if b"\0" in blob[:8000]:
                out["files"] += 1  # binary: a changed file with no line count
                continue
            out["insertions"] += blob.count(b"\n") + (0 if blob.endswith(b"\n") or not blob else 1)
            out["files"] += 1
    if not W.git(cwd, "rev-parse", "HEAD").ok:
        return out
    r = W.git(cwd, "diff", "HEAD", "--numstat", "--", ".", *FINGERPRINT_EXCLUDE)
    if r.ok:
        for line in r.out.splitlines():
            # `--numstat` is exactly: additions, deletions, path. A rename emits the
            # path as `old => new`, which still lands in field three, so splitting on
            # tab and requiring three fields is correct for every form.
            parts = line.split("\t")
            if len(parts) < _NUMSTAT_FIELDS:
                continue
            add, rem = parts[0], parts[1]
            out["files"] += 1
            # `-` for a binary file, which has no line count. Counted as a changed
            # FILE with zero lines rather than skipped, so a commit of nothing but
            # binaries does not report "0 files changed".
            out["insertions"] += int(add) if add.isdigit() else 0
            out["deletions"] += int(rem) if rem.isdigit() else 0
    return out


def tree_fingerprint(cwd: Path) -> str:
    """What the working tree looked like, committed and uncommitted.

    `HEAD` alone is not enough -- the interesting state during a gate run is almost
    always dirty -- so this is the commit, plus the CONTENT of the tracked changes,
    plus the LIST of everything else git reports.

    Porcelain alone was not enough either, and that was a real bug. `git status
    --porcelain` is two status letters and a path: no content, no size, no mtime. So
    once a file was modified, every further edit to that same file produced
    byte-identical output and the digest did not move. That is not an edge case, it is
    the normal one -- at gate time the agent has been editing all along, so the tree is
    already dirty when the fingerprint is taken and the clean->dirty transition has
    already happened. `stale_evidence`'s own documented scenario ("run the tests, edit
    one more thing, complete") was therefore the case it could not see, and a gate that
    passed on superseded source read as fresh evidence.

    So `git diff HEAD` goes in as well: content-sensitive for tracked files, staged or
    not, and it adds no new noise because untracked paths were already in the porcelain
    listing. Cost is proportional to the SIZE OF THE CHANGES, not to the tree.

    `.ddflow/` is EXCLUDED throughout. Recording a gate outcome appends an event to it,
    so counting it made the fingerprint move as a direct consequence of taking it, and
    every completion then warned that the tree had changed since the gate ran. A
    warning that always fires is one nobody reads — and it would have fired on the
    ordinary path, not an edge case.

    UNTRACKED files are hashed separately, and that is not an optional extra: `git diff
    HEAD` never shows them and porcelain shows only `?? path`, so without this a brand
    new module -- untracked until its first commit, which is the ORDINARY state of
    agent work -- could be rewritten completely between the gate and the completion
    with the fingerprint unmoved. `git hash-object` without `-w` computes the ids and
    writes nothing, so the observer still does not change what it observes.

    **Known limit, stated rather than papered over:** `git diff` renders a TRACKED
    binary file as "Binary files ... differ" with no content, so a re-edit of an
    already-modified tracked binary is invisible. Untracked binaries are covered (they
    are hashed bytewise). The fix for the remaining case -- `--binary`, base85-encoding
    whole blobs into the digest -- costs far more than it buys here. Filed as B95.

    **This is no longer what a gate records** (B-uni-tree-identity): `tree_identity` is,
    and it names the tree by its content manifest (every path's mode and blob id), not by
    the rendered text of `git diff`. The fingerprint stays for evidence already in the log,
    as the fallback when no manifest can be taken, and for the "is the tree dirty" test.

    Not `write-tree` or `stash create`: both WRITE, and a function whose job is to
    observe must not change what it observes. Outside a repository it returns ""
    rather than raising, because a gate can legitimately run somewhere git does not
    reach, and a missing fingerprint is honest where a fabricated one is not.
    """
    head, parts = _dirt_parts(cwd)
    if not head:
        return ""
    return f"{head}+{_dirt(parts)}"


def _dirt_parts(cwd: Path) -> tuple[str, list[str]]:
    """(HEAD's first 12 characters, the three parts whose digest is a tree's "dirt"), or
    ("", []) outside a repository."""
    from ...infra import worktree as W

    head = W.git(cwd, "rev-parse", "HEAD")
    if not head.ok:
        return "", []
    status = W.git(cwd, "status", "--porcelain", "--", ".", *FINGERPRINT_EXCLUDE)
    # `HEAD` and not `--cached`: staged and unstaged changes are equally "not what is
    # committed", and a gate cares about the files on disk it just ran against.
    diff = W.git(cwd, "diff", "HEAD", "--", ".", *FINGERPRINT_EXCLUDE)
    parts = [status.out if status.ok else "", diff.out if diff.ok else ""]
    parts.append(_untracked_digest(cwd))
    return head.out.strip()[:12], parts


def _dirt(parts: list[str]) -> str:
    """ "clean" when every part is empty, else the digest of the parts joined by NUL."""
    # Each PART, not the joined body: `str.strip` keeps NUL, so a clean tree's body
    # "\0\0" never tested empty and every fingerprint read as dirty
    # (bug B-fingerprint-never-clean). `LEGACY_CLEAN` is what that recorded.
    return digest("\x00".join(parts)) if any(p.strip() for p in parts) else "clean"


def tree_identity(cwd: Path) -> str:
    """THE identity a gate records for the tree it ran on: ``<head12>+clean`` for a tree
    that holds exactly HEAD's files, else ``<head12>+<content id>`` (`source_tree`'s
    ``st:...``) -- the commit plus the exact content manifest, with `.ddflow/` left out.

    It replaces recording `tree_fingerprint` AND `source_tree` side by side: a clean tree is
    spelled exactly as the fingerprint spelled it, and a dirty one carries the manifest, so
    one id both distinguishes the tree and can be compared with the commit that later
    records it. When no manifest can be taken (an unmerged index, untracked files past the
    cap) the fingerprint's digest is the fallback. "" outside a repository.
    """
    head, parts = _dirt_parts(cwd)
    if not head:
        return ""
    if _dirt(parts) == "clean":
        return f"{head}+clean"
    manifest = source_tree(cwd)
    return f"{head}+{manifest or _dirt(parts)}"


def recorded_content(tree_sha: str, source_tree_field: str = "") -> str:
    """The content id a recorded identity names, or "" when it names none: the
    ``source_tree`` field an older gate recorded beside its fingerprint, else the ``st:...``
    a `tree_identity` carries. A clean or fingerprint-spelled ``tree_sha`` names a commit,
    which the caller reads as a tree."""
    if source_tree_field:
        return source_tree_field
    _base, _sep, dirt = tree_sha.partition("+")
    return dirt if dirt.startswith("st:") else ""


#: The "dirt" every CLEAN tree recorded before bug B-fingerprint-never-clean was fixed:
#: the digest of the two NULs joining three empty parts. Read as "clean".
LEGACY_CLEAN = "95e0c70caf8cc336"  # == digest("\x00\x00"), pinned by a test


def normal_fingerprint(fp: str) -> str:
    """``fp`` with the pre-fix spelling of a clean tree read as `clean`."""
    base, sep, dirt = fp.partition("+")
    return f"{base}+clean" if sep and dirt == LEGACY_CLEAN else fp


def _git_z(cwd: Path | str, *args: str) -> list[str] | None:
    """NUL-separated git output as names exactly as the filesystem spells them, or None
    when git failed -- "could not tell", never "nothing"."""
    return GIT.run(cwd, *args, binary=True).paths()


_ours = is_state  # `.ddflow/` is ddflow's bookkeeping, not the work -- see FINGERPRINT_EXCLUDE


#: One file's identity inside a content tree: (mode, blob id), as git stores it.
TreeEntries = dict[str, tuple[str, str]]


def commit_tree_entries(cwd: Path | str, rev: str) -> TreeEntries | None:
    """Every path of commit ``rev`` with its mode and blob id, `.ddflow/` left out."""
    rows = _git_z(cwd, "ls-tree", "-r", "-z", "--full-tree", rev)
    if rows is None:
        return None
    out: TreeEntries = {}
    for row in rows:
        meta, _, path = row.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and path and not _ours(path):  # noqa: PLR2004 -- mode type id
            out[path] = (parts[0], parts[2])
    return out


def _blob_id(data: bytes, fmt: str) -> str:
    """git's blob id for ``data``, computed here: `hash-object` would follow a symlink."""
    return content_digest(b"blob %d\0" % len(data) + data, "sha256" if fmt == "sha256" else "sha1")


def worktree_entries(cwd: Path | str) -> TreeEntries | None:
    """What the working tree holds, as the commit that recorded it exactly would.

    The index's entries, overridden by every file whose working copy differs from the
    index and by every untracked, non-ignored file -- each hashed with `git hash-object`
    WITHOUT `-w`, so nothing is written (the same rule `tree_fingerprint` keeps). Whole
    repository, from its top level, `.ddflow/` excluded.

    None when it cannot be told: not a repository, an unmerged index, or more untracked
    files than MAX_UNTRACKED_HASHED.
    """
    from ...infra import worktree as W

    top = W.git(cwd, "rev-parse", "--show-toplevel")
    if not top.ok:
        return None
    root = Path(top.out)
    index = _git_z(root, "ls-files", "-s", "-z")
    changed = _git_z(root, "diff", "--name-only", "-z", "--no-renames")
    untracked = GIT.files(root, "untracked")
    if index is None or changed is None or untracked is None:
        return None
    # The cap counts the work's own files: `.ddflow/` is never hashed, so its untracked
    # bookkeeping must not turn content evidence off (Bf0c754ec45).
    untracked = [p for p in untracked if not _ours(p)]
    if len(untracked) > MAX_UNTRACKED_HASHED:
        return None
    out = _index_entries(index)
    if out is None:
        return None
    fmt = W.git(root, "rev-parse", "--show-object-format").out or "sha1"
    filemode = W.git(root, "config", "--bool", "core.fileMode").out != "false"
    to_hash: list[tuple[str, str]] = []
    for path in [*changed, *untracked]:
        if _ours(path):
            continue
        path = path.rstrip("/")  # noqa: PLW2901 -- an untracked nested repository
        kind, value = _working_entry(root, path, out.get(path), fmt, filemode)
        if kind == "drop":
            out.pop(path, None)
        elif kind == "set":
            out[path] = value
        elif kind == "hash":
            to_hash.append((path, value))
    return out if _hash_into(root, out, to_hash) else None


def _working_entry(
    root: Path, path: str, prior: tuple[str, str] | None, fmt: str, filemode: bool
) -> tuple[str, Any]:
    """How ``path``'s working copy enters the tree, as `git add` would record it:
    ("drop", None), ("set", (mode, id)), ("hash", mode) or ("keep", None)."""
    from ...infra import worktree as W

    full = root / path
    if not os.path.lexists(full):
        return "drop", None
    if full.is_symlink():
        return "set", ("120000", _blob_id(os.fsencode(os.readlink(full)), fmt))
    if full.is_dir():
        # A repository (a submodule, or a nested repository `git add` would record as
        # one) is its checked-out commit. Any other directory has REPLACED a tracked
        # file of that name: the file is gone, and what is under the directory arrives
        # as untracked paths of its own.
        if (full / ".git").exists():
            head = W.git(full, "rev-parse", "HEAD")
            return ("set", ("160000", head.out)) if head.ok else ("keep", None)
        return "drop", None
    if filemode:
        # git's own test: the OWNER's execute bit (S_IXUSR), not any of the three.
        return "hash", "100755" if full.stat().st_mode & 0o100 else "100644"
    # core.fileMode=false: git ignores the bit -- a regular file keeps the index's
    # mode; anything else (new, or replacing a symlink or a submodule) is 100644.
    regular = prior is not None and prior[0] in ("100644", "100755")
    return "hash", prior[0] if prior is not None and regular else "100644"


def _index_entries(rows: list[str]) -> TreeEntries | None:
    """`git ls-files -s -z` rows as entries; None for an unmerged index (no one tree)."""
    out: TreeEntries = {}
    for row in rows:
        meta, _, path = row.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or not path or parts[2] != "0":  # noqa: PLR2004 -- mode id stage
            return None
        if not _ours(path):
            out[path] = (parts[0], parts[1])
    return out


def _hash_into(root: Path, out: TreeEntries, to_hash: list[tuple[str, str]]) -> bool:
    """Blob ids for ``to_hash`` ((path, mode)) into ``out``, without writing objects."""
    from ...infra import worktree as W

    for i in range(0, len(to_hash), 200):
        chunk = to_hash[i : i + 200]
        r = W.git(root, "hash-object", "--", *(p for p, _ in chunk))
        ids = r.out.splitlines()
        if not r.ok or len(ids) != len(chunk):
            return False
        for (path, mode), oid in zip(chunk, ids, strict=True):
            out[path] = (mode, oid)
    return True


def content_id(entries: TreeEntries | None) -> str:
    """One id for a content tree; "" when there is none to name."""
    if entries is None:
        return ""
    body = "\n".join(f"{m} {o} {p}" for p, (m, o) in sorted(entries.items()))
    return "st:" + content_digest(body, "blake2b", size=16, errors="surrogateescape")


def source_tree(cwd: Path | str) -> str:
    """The CONTENT the working tree holds -- every path's mode and blob id.

    `tree_fingerprint` names a commit plus dirt, so the same files under another commit
    (the merge commit, a rebased head, the branch tip after the edits were committed)
    read as a different tree. This names only content: a gate run on uncommitted edits
    has the same source tree as the commit that later records exactly those edits.
    """
    return content_id(worktree_entries(cwd))


def commit_source_tree(cwd: Path | str, rev: str) -> str:
    """`source_tree` of a commit, comparable with one taken from a working tree."""
    return content_id(commit_tree_entries(cwd, rev))


def differing_paths(a: TreeEntries, b: TreeEntries) -> list[str]:
    return sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))


def digest(text: str) -> str:
    return content_digest(text, "blake2b", size=8, errors="replace")
