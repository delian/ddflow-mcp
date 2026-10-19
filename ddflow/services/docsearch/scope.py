"""Which files the documentation index holds, and how it is brought up to date.

`resolve` is the scope: TRACKED files only (``git ls-files``), matching the include globs and
not the excludes, under a size cap and not binary; ddflow's generated views only when asked.
`from_config` reads the globs the way ``docs check`` does: ``[docs].check_paths`` if set, else
``[enforce].doc_globs`` minus ``[enforce].doc_exclude`` (D-doc-search).

`refresh` makes the index match the scope: a file whose stat is unchanged is not read; one
whose content hash is unchanged is not rechunked; a changed one is chunked, REDACTED, and
replaced in one transaction; a vanished or out-of-scope one is dropped. Text is redacted
before it enters the index, so a search cannot surface a secret.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ...core import docchunk
from ...core.digest import content_digest
from ...infra import git
from ...infra.docindex import DocIndex, Known
from ..docsync import glob_regex
from ..shared_files import export_targets

MAX_BYTES = 1_000_000
GENERATED_MARK = b"<!-- ddflow:generated doc="
BINARY_PROBE = 8192
Redact = Callable[[str], str]


@dataclass(frozen=True)
class Scope:
    include: tuple[str, ...] = ("**/*.md", "**/*.rst", "**/*.adoc")
    exclude: tuple[str, ...] = ()
    generated: tuple[str, ...] = ()  # export targets, left out unless include_generated
    include_generated: bool = False
    max_bytes: int = MAX_BYTES


@dataclass
class Resolved:
    files: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # path -> why


def from_config(cfg: object, *, include_generated: bool = False) -> Scope:
    """The scope a project's configuration describes (the ``docs check`` rule)."""
    docs, enforce = cfg.docs, cfg.enforce  # type: ignore[attr-defined]
    include = tuple(docs.check_paths) or tuple(enforce.doc_globs)
    return Scope(
        include,
        tuple(enforce.doc_exclude) if not docs.check_paths else (),
        tuple(export_targets(cfg, generated_only=True)),  # type: ignore[arg-type]
        include_generated,
    )


def _binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(BINARY_PROBE)
    except OSError:
        return True


def resolve(repo: Path | str, scope: Scope) -> Resolved:
    """Tracked files in scope, sorted; what was left out for size, binary or generation is
    in ``skipped`` with the reason. `git` failing raises ``OSError``: no list is not an empty one."""
    root = Path(repo)
    tracked = git.files(root, "tracked")
    if tracked is None:
        raise OSError(f"git could not list the tracked files of {root}")
    inc = [glob_regex(g) for g in scope.include]
    exc = [glob_regex(g) for g in scope.exclude]
    gen = set(scope.generated)
    out = Resolved()
    for rel in sorted(tracked):
        if not any(r.fullmatch(rel) for r in inc) or any(r.fullmatch(rel) for r in exc):
            continue
        path = root / rel
        try:
            st = path.lstat()
        except OSError:
            continue  # tracked but deleted in the tree: it is a vanished file
        if not path.is_file() or path.is_symlink():
            out.skipped[rel] = "not a regular file"
        elif st.st_size > scope.max_bytes:
            out.skipped[rel] = f"{st.st_size} bytes, over the {scope.max_bytes} cap"
        elif _binary(path):
            out.skipped[rel] = "binary"
        elif not scope.include_generated and (rel in gen or _is_generated(path)):
            out.skipped[rel] = "generated view"
        else:
            out.files.append(rel)
    return out


def _is_generated(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.readline(512).startswith(GENERATED_MARK)
    except OSError:
        return False


@dataclass
class Refresh:
    """What a refresh did, by path."""

    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)  # stat moved, content did not
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    skipped: dict[str, str] = field(default_factory=dict)
    rebuilt: bool = False


def refresh(
    index: DocIndex,
    repo: Path | str,
    scope: Scope,
    *,
    redact: Redact,
    doctype_of: Callable[[str], str] = lambda p: "",
    max_chars: int = 1500,
    overlap: int = 150,
    only: Sequence[str] | None = None,
) -> Refresh:
    """Bring ``index`` in line with ``scope``. ``only`` limits the work to those paths (a
    watcher's batch); anything else in the index is left alone then."""
    root = Path(repo)
    res = resolve(root, scope)
    want = set(res.files)
    out = Refresh(skipped=dict(res.skipped))
    with index.writer() as w:
        out.rebuilt = w.rebuilt
        have = w.known()
        pool = set(only) if only is not None else None
        gone = [p for p in have if p not in want and (pool is None or p in pool)]
        w.drop(gone)
        out.removed = sorted(gone)
        for rel in res.files:
            if pool is not None and rel not in pool:
                continue
            _one(w, root, rel, have.get(rel), out, redact, doctype_of(rel), max_chars, overlap)
    return out


def _one(w, root: Path, rel, old: Known | None, out: Refresh, redact, doctype, max_chars, overlap):
    path = root / rel
    try:
        st = path.stat()
        data = None
        if old is None or (old.size, old.mtime_ns, old.inode) != (
            st.st_size,
            st.st_mtime_ns,
            st.st_ino,
        ):
            data = path.read_bytes()
    except OSError:
        out.skipped[rel] = "unreadable"
        return
    if data is None:
        out.unchanged += 1
        return
    sha = content_digest(data)
    new = Known(len(data), st.st_mtime_ns, st.st_ino, sha)
    if old is not None and old.sha256 == sha:
        w.touch(rel, new)
        out.touched.append(rel)
        return
    text: str | bytes = data
    try:  # the whole file, before chunking: a key block can span chunks
        text = redact(data.decode("utf-8"))
    except UnicodeDecodeError:
        pass  # the chunker reports it as not UTF-8
    chunked = docchunk.chunk(rel, text, doctype=doctype, max_chars=max_chars, overlap=overlap)
    if chunked.skipped:
        out.skipped[rel] = chunked.skipped
        w.drop([rel])
        return
    w.put(rel, new, doctype, chunked.chunks)
    (out.added if old is None else out.changed).append(rel)


__all__ = ["Refresh", "Resolved", "Scope", "from_config", "refresh", "resolve"]
