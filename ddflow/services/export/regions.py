"""Named regions: one marker grammar, a parser and a splicer (B-tpl-regions, D-doc-regions).

A region is::

    <!-- ddflow:begin <doc>/<region> sha=<12 hex> -->
    ...body...
    <!-- ddflow:end <doc>/<region> -->

``sha`` is the digest of the body (``frame.body_digest``), so a hand edit inside a region is
visible. A template is both the skeleton of a new file and the source of every region body:
``create`` writes the full render; ``update`` renders, extracts each region body and splices
it into the file by name. Text outside the markers is never touched (byte for byte, CRLF
included); a template region the file lacks is reported, never inserted.

Two legacy dialects are read and rewritten into the new grammar on the first write:
``<!-- ddflow:begin doc=X body-sha256=H -->`` (region name ``body``) and the bare
``<!-- DDFLOW:BEGIN (...) -->`` block (adopt's; only when the caller names its document).

``parse`` / ``splice`` are pure; ``write_named`` is the locked file write.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from ...infra import tomlcfg
from . import frame as F
from . import write as W
from .query import ExportError
from .region_marks import render_region

DEFAULT_REGION = "body"  # the name a legacy single-region marker gets
_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
_NEW_BEGIN = re.compile(
    rf"<!-- ddflow:begin (?P<doc>{_NAME})/(?P<name>{_NAME}) sha=(?P<sha>[0-9a-f]{{{F.DIGEST_LEN}}}) -->"
)
_NEW_END = re.compile(rf"<!-- ddflow:end (?P<doc>{_NAME})/(?P<name>{_NAME}) -->")
_OLD_BEGIN = re.compile(
    rf"<!-- ddflow:begin doc=(?P<doc>{_NAME}) body-sha256=(?P<sha>[0-9a-f]{{{F.DIGEST_LEN}}})"
    r"(?: [A-Za-z0-9_-]+=\S+)* -->"
)
_OLD_END = re.compile(rf"<!-- ddflow:end doc=(?P<doc>{_NAME}) -->")
_BARE_BEGIN = re.compile(r"<!-- DDFLOW:BEGIN\b[^>]*-->")
_BARE_END = re.compile(r"<!-- DDFLOW:END -->")


class RegionError(ExportError):
    """Markers that cannot be read (duplicate, nested, unbalanced, mismatched): refused."""

    def __init__(self, message: str) -> None:
        super().__init__(message, W.EXIT_REFUSED)


@dataclass(frozen=True)
class Region:
    doc: str
    name: str
    sha: str  # recorded digest; "" for a bare legacy block (it records none)
    begin: int  # offset of the begin line
    body: int  # offset just after the begin line (and its newline)
    end: int  # offset of the end line
    stop: int  # offset just after the end line (and its newline)
    dialect: str  # "new" | "old" | "bare"
    text: str = field(compare=False, repr=False, default="")

    @property
    def content(self) -> str:
        return self.text[self.body : self.end]

    @property
    def edited(self) -> bool:
        return bool(self.sha) and F.body_digest(self.content) != self.sha


def _lines(text: str):
    pos = 0
    for line in text.splitlines(keepends=True):
        yield pos, line.rstrip("\r\n"), pos + len(line)
        pos += len(line)


@dataclass(frozen=True)
class _Mark:
    kind: str  # "begin" | "end"
    dialect: str  # "new" | "old" | "bare"
    doc: str
    name: str
    sha: str = ""


def _classify(line: str, bare_doc: str) -> _Mark | None:
    """The marker a line is, or None."""
    for kind, dialect, rx in (
        ("begin", "new", _NEW_BEGIN),
        ("begin", "old", _OLD_BEGIN),
        ("end", "new", _NEW_END),
        ("end", "old", _OLD_END),
    ):
        m = rx.fullmatch(line)
        if m:
            g = m.groupdict()
            return _Mark(kind, dialect, g["doc"], g.get("name") or DEFAULT_REGION, g.get("sha", ""))
    if bare_doc:
        for kind, rx in (("begin", _BARE_BEGIN), ("end", _BARE_END)):
            if rx.fullmatch(line):
                return _Mark(kind, "bare", bare_doc, DEFAULT_REGION)
    return None


def parse(text: str, *, bare_doc: str = "") -> list[Region]:
    """Every region in ``text``, in file order. Raises ``RegionError`` for a duplicate
    (doc, name), a nested or unbalanced pair, or an end that names another region."""
    found: list[Region] = []
    cur: tuple[_Mark, int, int] | None = None  # the open begin, its offset, its body offset
    for start, line, nxt in _lines(text):
        mark = _classify(line, bare_doc)
        if mark is None:
            continue
        if mark.kind == "begin":
            if cur is not None:
                raise RegionError(
                    f"ddflow region {cur[0].doc}/{cur[0].name} is not closed before the next begin"
                )
            cur = (mark, start, nxt)
            continue
        if cur is None or (cur[0].doc, cur[0].name, cur[0].dialect) != (
            mark.doc,
            mark.name,
            mark.dialect,
        ):
            raise RegionError(f"unbalanced ddflow end marker: {line.strip()}")
        b = cur[0]
        found.append(Region(b.doc, b.name, b.sha, cur[1], cur[2], start, nxt, b.dialect, text))
        cur = None
    if cur is not None:
        raise RegionError(f"ddflow region {cur[0].doc}/{cur[0].name} is never closed")
    seen: set[tuple[str, str]] = set()
    for r in found:
        if (r.doc, r.name) in seen:
            raise RegionError(f"duplicate ddflow region {r.doc}/{r.name}")
        seen.add((r.doc, r.name))
    return found


def extract(rendered: str, doc: str) -> dict[str, str]:
    """The ``doc`` region bodies of a render, by name (order kept)."""
    return {r.name: r.content for r in parse(rendered) if r.doc == doc}


@dataclass(frozen=True)
class Splice:
    text: str
    updated: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()  # in the template, not in the file: reported, not inserted
    orphans: tuple[str, ...] = ()  # in the file, not in the template: left as they are
    migrated: tuple[str, ...] = ()  # legacy markers rewritten into the new grammar
    edited: tuple[str, ...] = ()  # hand-edited since written (refused without force)


def splice(old: str, doc: str, bodies: Mapping[str, str], *, bare_doc: str = "") -> Splice:
    """``old`` with the ``doc`` region bodies replaced by ``bodies``; all else byte-identical."""
    mine = [r for r in parse(old, bare_doc=bare_doc or "") if r.doc == doc]
    by_name = {r.name: r for r in mine}
    out: list[str] = []
    cur = 0
    updated: list[str] = []
    same: list[str] = []
    migrated: list[str] = []
    edited: list[str] = []
    for r in mine:
        if r.name not in bodies:
            continue
        new = render_region(doc, r.name, bodies[r.name])
        if "\r\n" in old[r.begin : r.body]:  # a CRLF file keeps its line ends
            new = new.replace("\n", "\r\n")
        if not old[r.begin : r.stop].endswith("\n"):  # end marker on the last, unterminated line
            new = new.rstrip("\r\n")
        out.append(old[cur : r.begin])
        out.append(new)
        cur = r.stop
        if r.edited:
            edited.append(r.name)
        if r.dialect != "new":
            migrated.append(r.name)
        (same if old[r.begin : r.stop] == new else updated).append(r.name)
    out.append(old[cur:])
    return Splice(
        "".join(out),
        tuple(updated),
        tuple(same),
        tuple(n for n in bodies if n not in by_name),
        tuple(n for n in by_name if n not in bodies),
        tuple(migrated),
        tuple(edited),
    )


def write_named(
    repo: Path | str,
    target: str,
    doc: str,
    rendered: str,
    *,
    check: bool = False,
    diff: bool = False,
    force: bool = False,
    bare_doc: str = "",
) -> W.WriteResult:
    """Create ``target`` from ``rendered`` (the whole render), or splice its regions.

    The file is written under the export lock in one atomic replace. A region the file has
    that was edited by hand is refused without ``force``. ``notes`` on the result report
    missing, migrated and orphaned regions.
    """
    bodies = extract(rendered, doc)
    if not bodies:
        raise RegionError(f"the {doc!r} template emits no named region; use mode whole or region")
    W.safe_target(repo, target)
    with tomlcfg.locked(W._lock_path(repo)):
        path, rel = W.safe_target(repo, target)
        old = W._read(path)
        if old is None:
            return W._finish(path, rel, None, rendered, check=check, diff=diff, refuse="")
        sp = splice(old, doc, bodies, bare_doc=bare_doc)
        refuse = ""
        if sp.edited and not force:
            refuse = (
                f"region(s) {', '.join(sp.edited)} of {rel} were edited by hand since ddflow "
                "wrote them; refusing to overwrite (--force to replace)"
            )
        res = W._finish(path, rel, old, sp.text, check=check, diff=diff, refuse=refuse)
        return replace(res, notes=_notes(doc, sp))


def _notes(doc: str, sp: Splice) -> tuple[str, ...]:
    out = [
        f"region {doc}/{n} is in the template but not in the file; not inserted" for n in sp.missing
    ]
    out += [f"region {doc}/{n}: legacy marker rewritten to the new grammar" for n in sp.migrated]
    out += [f"region {doc}/{n} is not in the template; left as it is" for n in sp.orphans]
    return tuple(out)
