"""Writing an exported document to the repository (B-export-write, decision D-export).

Three update modes, one set of rules. Everything that touches the file happens under ONE
lock (``.ddflow/local/.export.lock``, via ``infra.tomlcfg.locked``) and ends in ONE atomic
replace (``tomlcfg.atomic_write``): the hand-edit check and the write are the same critical
section, so a concurrent writer can neither slip an edit between them nor interleave.

``whole`` (``write_whole``)
    The file is entirely generated: header, notice, body (``frame.frame``). Refused
    (exit 3, with the diff) when the existing file was edited by hand since ddflow wrote it
    (its body no longer matches the header digest), when it carries no ddflow header at all
    (not ours), or when a different document kind generated it; ``force`` overrides.

``region`` (``write_region``)
    Only a marked region of a hand-written file::

        <!-- ddflow:begin doc=<kind> body-sha256=<12 hex> -->
        ...generated...
        <!-- ddflow:end doc=<kind> -->

    The text around the region is kept byte for byte. A missing marker, a duplicate, or an
    unbalanced or reversed pair is refused (``force`` may only ADD a missing region, at the
    end of the file; it never repairs duplicates or unbalanced markers). An edited region
    (digest mismatch) is refused without ``force``.

``append`` (``append_entries``)
    A growing log: entries are appended after the last exported event id recorded in the
    header (``last=<event id>``); lines already written are never rewritten (only the header's
    digest and ``last`` move). The target is registered in ``[lease].append_only_globs`` so git
    union-merges it.

``check`` / ``diff``
    ``check=True`` writes nothing and reports ``code`` 0 (fresh: the file is exactly what
    would be written) or 1 (stale: absent, different, hand-edited or not ddflow's); a file
    that cannot be read raises ``ExportError`` code 2. ``diff=True`` writes nothing and returns
    the unified diff of what a write would change.

Path safety (``safe_target``): a target must be a relative path inside the repository; absolute
paths, ``..``, any ``.git`` or ``.ddflow`` component, and symlinks (the file itself or any
parent directory) are refused (exit 3) before anything is touched, and re-checked inside the
lock.
"""

from __future__ import annotations

import difflib
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ...infra import tomlcfg
from . import frame as F
from .query import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError

EXIT_STALE = 1  # `--check`: the file is not what a write would produce


class Refused(ExportError):
    """A write ddflow will not do. ``diff`` shows what it would have changed, when known."""

    def __init__(self, message: str, diff: str = "") -> None:
        super().__init__(message, EXIT_REFUSED)
        self.diff = diff


@dataclass(frozen=True)
class WriteResult:
    path: Path  # absolute
    rel: str  # as given, normalized, '/'-separated
    action: str  # created | updated | unchanged | fresh | stale | diff
    code: int = 0  # `--check`: 0 fresh, 1 stale; always 0 otherwise
    diff: str = ""
    registered: bool = False  # append mode: the target was added to append_only_globs


# -- path safety ----------------------------------------------------------------------


def safe_target(repo: Path | str, target: str) -> tuple[Path, str]:
    """``(absolute path, normalized relative path)`` for ``target``, or ``Refused``.

    Nothing is created. Refuses: an empty or NUL-bearing path, an absolute path, ``..``,
    any ``.git`` / ``.ddflow`` component, a symlink anywhere along the way (a symlinked
    parent included), and a target that exists but is not a regular file.
    """
    if not target or "\0" in target:
        raise Refused(f"export target {target!r} is empty or contains a NUL byte")
    if target.startswith(("/", "\\")) or os.path.isabs(target) or re.match(r"^[A-Za-z]:", target):
        raise Refused(f"export target {target!r} is an absolute path; give a path inside the repo")
    parts = [p for p in PurePosixPath(target.replace("\\", "/")).parts if p != "."]
    if not parts:
        raise Refused(f"export target {target!r} names no file")
    for p in parts:
        if p == "..":
            raise Refused(f"export target {target!r} leaves the repository ('..')")
        if p.lower() in (".git", ".ddflow"):
            raise Refused(
                f"export target {target!r} is inside {p}/, which ddflow does not export to"
            )
    root = Path(os.path.realpath(repo))
    cur = root
    for i, p in enumerate(parts):
        cur = cur / p
        last = i == len(parts) - 1
        if cur.is_symlink():
            raise Refused(f"export target {target!r}: {cur.relative_to(root)} is a symlink")
        if cur.exists():
            mode = cur.lstat().st_mode
            if last and not stat.S_ISREG(mode):
                raise Refused(f"export target {target!r} exists and is not a regular file")
            if not last and not stat.S_ISDIR(mode):
                raise Refused(
                    f"export target {target!r}: {cur.relative_to(root)} is not a directory"
                )
    return cur, "/".join(parts)


# -- shared plumbing ------------------------------------------------------------------


def _lock_path(repo: Path | str) -> Path:
    return Path(repo) / ".ddflow" / "local" / "export"  # -> .ddflow/local/.export.lock


def _read(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")  # not read_text: that folds CRLF to LF
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ExportError(f"could not read {path}: {exc}", EXIT_UNAVAILABLE) from exc


def unified(old: str | None, new: str, rel: str) -> str:
    return "".join(
        difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel}" if old is not None else "/dev/null",
            tofile=f"b/{rel}",
        )
    )


def _commit(path: Path, text: str) -> None:
    """Atomic replace that keeps the existing file's permission bits."""
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    tomlcfg.atomic_write(path, text)
    if mode is None:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    os.chmod(path, mode)


def _finish(
    path: Path, rel: str, old: str | None, new: str, *, check: bool, diff: bool, refuse: str
) -> WriteResult:
    """Decide and (maybe) write. ``refuse`` non-empty means the write needs ``force``.

    Called inside the lock with the path already re-verified.
    """
    d = unified(old, new, rel)
    if check:
        fresh = old == new
        return WriteResult(path, rel, "fresh" if fresh else "stale", 0 if fresh else EXIT_STALE, d)
    if diff:
        return WriteResult(path, rel, "diff", 0, d)
    if refuse:
        raise Refused(refuse, d)
    if old == new:
        return WriteResult(path, rel, "unchanged")
    _commit(path, new)
    return WriteResult(path, rel, "created" if old is None else "updated", 0, d)


# -- whole file -----------------------------------------------------------------------


def write_whole(
    repo: Path | str,
    target: str,
    document: str,
    *,
    check: bool = False,
    diff: bool = False,
    force: bool = False,
) -> WriteResult:
    """Write ``document`` (a framed document: ``registry.render_document(..., max_bytes=0)``)."""
    head, _ = F.split(document)
    if head is None:
        raise ExportError("write_whole needs a framed document (header + body)", EXIT_REFUSED)
    path, rel = safe_target(repo, target)
    with tomlcfg.locked(_lock_path(repo)):
        path, rel = safe_target(repo, target)  # again, under the lock
        old = _read(path)
        refuse = ""
        if old is not None and old != document and not force:
            old_head, _ = F.split(old)
            edited = F.hand_edited(old)
            if old_head is None:
                refuse = f"{rel} has no ddflow header: it is not a generated file; refusing to overwrite (--force to replace)"
            elif edited:
                refuse = f"{rel} was edited by hand since ddflow wrote it (body does not match its digest); refusing to overwrite (--force to replace)"
            elif old_head.doc != head.doc:
                refuse = f"{rel} was generated as {old_head.doc!r}, not {head.doc!r}; refusing to overwrite (--force to replace)"
        return _finish(path, rel, old, document, check=check, diff=diff, refuse=refuse)


# -- marker region --------------------------------------------------------------------


def _begin(doc: str, digest: str) -> str:
    return f"<!-- ddflow:begin doc={doc} body-sha256={digest} -->"


def _end(doc: str) -> str:
    return f"<!-- ddflow:end doc={doc} -->"


def _region_pattern(doc: str, which: str) -> re.Pattern[str]:
    d = re.escape(doc)
    if which == "begin":
        return re.compile(
            rf"^<!-- ddflow:begin doc={d} body-sha256=(?P<sha>[0-9a-f]{{{F.DIGEST_LEN}}}) -->\r?$",
            re.M,
        )
    return re.compile(rf"^<!-- ddflow:end doc={d} -->\r?$", re.M)


def region_text(doc: str, body: str) -> str:
    """The marked region for ``body`` (begin line, body, end line), ending in a newline."""
    body = F.normalize(body)
    return f"{_begin(doc, F.body_digest(body))}\n{body}{_end(doc)}\n"


def write_region(
    repo: Path | str,
    target: str,
    doc: str,
    body: str,
    *,
    check: bool = False,
    diff: bool = False,
    force: bool = False,
) -> WriteResult:
    """Replace the ``doc`` region of ``target`` with ``body``; the rest is kept byte for byte."""
    path, rel = safe_target(repo, target)
    new_region = region_text(doc, body)
    with tomlcfg.locked(_lock_path(repo)):
        path, rel = safe_target(repo, target)
        old = _read(path)
        if old is None:
            return _finish(path, rel, None, new_region, check=check, diff=diff, refuse="")
        begins = list(_region_pattern(doc, "begin").finditer(old))
        ends = list(_region_pattern(doc, "end").finditer(old))
        if not begins and not ends:
            joined = (
                old
                + ("" if not old or old.endswith("\n") else "\n")
                + ("\n" if old else "")
                + new_region
            )
            refuse = (
                ""
                if force
                else (
                    f"{rel} has no ddflow region for {doc!r}: add the markers "
                    f"({_begin(doc, '<digest>')} ... {_end(doc)}) where it should go, or --force to append one"
                )
            )
            return _finish(path, rel, old, joined, check=check, diff=diff, refuse=refuse)
        if len(begins) != 1 or len(ends) != 1 or begins[0].end() > ends[0].start():
            problem = "duplicate" if len(begins) > 1 or len(ends) > 1 else "unbalanced or reversed"
            raise Refused(
                f"{rel} has {problem} ddflow markers for {doc!r}; fix them by hand (--force does not repair markers)"
            )
        b, e = begins[0], ends[0]
        current = old[b.end() + 1 : e.start()]  # between the begin line and the end line
        refuse = ""
        if F.body_digest(current) != b.group("sha") and not force:
            refuse = f"the {doc!r} region of {rel} was edited by hand since ddflow wrote it; refusing to overwrite (--force to replace)"
        new = old[: b.start()] + new_region + old[e.end() + 1 :]
        return _finish(path, rel, old, new, check=check, diff=diff, refuse=refuse)


# -- append-only ----------------------------------------------------------------------


def last_exported(repo: Path | str, target: str) -> str:
    """The ``last=<event id>`` recorded in ``target``'s header ("" if absent or not ours)."""
    path, _ = safe_target(repo, target)
    old = _read(path)
    head, _ = F.split(old) if old is not None else (None, "")
    return head.extra.get("last", "") if head else ""


def append_entries(
    repo: Path | str,
    target: str,
    doc: str,
    produce: Callable[[str], tuple[str, str]],
    *,
    version: str = "",
    check: bool = False,
    diff: bool = False,
    force: bool = False,
    register: bool = True,
) -> WriteResult:
    """Append new entries after the last exported event id.

    ``produce(last)`` is called INSIDE the lock with the ``last`` recorded in the file's
    header ("" for a new file) and returns ``(entries_text, new_last_event_id)``; empty
    entries mean nothing to add. Old lines are never rewritten: the new body is the old
    body plus the entries, and only the header (digest, ``last``) changes.
    """
    path, rel = safe_target(repo, target)
    if not version:
        import ddflow

        version = str(ddflow.__version__)
    with tomlcfg.locked(_lock_path(repo)):
        path, rel = safe_target(repo, target)
        old = _read(path)
        head, old_body = F.split(old) if old is not None else (None, "")
        refuse = ""
        if old is not None and head is None and not force:
            refuse = f"{rel} has no ddflow header: it is not a generated file; refusing to append (--force to adopt it as the log's first lines)"
        elif head is not None and F.hand_edited(old or "") and not force:
            refuse = f"{rel} was edited by hand since ddflow wrote it; refusing to append (--force to continue anyway)"
        elif head is not None and head.doc != doc and not force:
            refuse = (
                f"{rel} was generated as {head.doc!r}, not {doc!r}; refusing to append (--force)"
            )
        last = head.extra.get("last", "") if head else ""
        entries, new_last = produce(last)
        if not entries:  # nothing new since `last`
            if check:  # a file ddflow would refuse to append to is not fresh either
                return WriteResult(
                    path, rel, "stale" if refuse else "fresh", EXIT_STALE if refuse else 0
                )
            return WriteResult(path, rel, "unchanged")
        prior = old_body if head is not None else (old or "")
        if prior and not prior.endswith("\n"):
            prior += "\n"
        extra = dict(head.extra) if head else {}
        extra["last"] = new_last
        new = F.frame(prior + entries, doc, version, extra)
        result = _finish(path, rel, old, new, check=check, diff=diff, refuse=refuse)
    registered = False
    if register and result.action in ("created", "updated"):
        registered = register_append_only(repo, rel)
    return WriteResult(result.path, result.rel, result.action, result.code, result.diff, registered)


def register_append_only(repo: Path | str, rel: str) -> bool:
    """Add ``rel`` to the committed ``[lease].append_only_globs`` (and its ``merge=union``
    line). True if it was added, False if already there."""
    from ..configwrite import _toml_literal, _write_config
    from ..shared_files import committed_append_only, sync_attributes

    have = committed_append_only(Path(repo))
    if rel in have:
        return False
    items = ", ".join(_toml_literal(g) for g in [*have, rel])
    err, _ = _write_config(
        Path(repo), [("lease.append_only_globs", f"[{items}]")], check_workflow=False
    )
    if err:
        raise ExportError(
            f"could not register {rel} in [lease].append_only_globs: {err}", EXIT_UNAVAILABLE
        )
    sync_attributes(Path(repo))
    return True
