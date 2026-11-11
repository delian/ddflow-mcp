"""File-system operations that are not a port: path helpers, JSON reads and the doc-region
`Managed` stamping, beside the re-export of the `Fsio` port (ADP-migrate-homes).

The `Fsio` port's atomic writes, temporary files and locks now live in `ddflow.adapters.fsio`,
re-exported here so `from ddflow.infra.fsio import atomic_write` keeps working until the call
sites move. The path helpers delegate to `ddflow.adapters.fs`; `Managed` stamps a region with
the writing version and format level, so it keeps `ddflow`'s version constants and
`core.events.is_older`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ddflow import FORMAT_LEVEL, __version__
from ddflow.adapters import fs as _fs
from ddflow.adapters.fsio import *  # noqa: F403  # the Fsio port's names, re-exported
from ddflow.core.digest import content_digest
from ddflow.core.events import is_older


def repo_rel(
    repo: Path | str, path: Path | str, *, as_given: bool = False, strict: bool = True
) -> str | None:
    """`path` relative to `repo` as a POSIX string. Outside the repository: None when
    `strict` (the default), else `path` itself, as given, unchanged.

    By default both sides are resolved, so a repo reached through a symlink and a path
    that was resolved (or the other way round) still compare equal, and "outside" means
    the real file is outside. `worktree.store_path`, `enforce._rel` and the pins report
    make their "inside the repository, and as what" decision here.

    `as_given=True` is for REPORTS: "inside" then also means "named inside". It first
    tries `path` exactly as written, against `repo` as written and as resolved, and
    resolves only when neither holds it, so a file the operator listed (a symlinked
    suite or document, or one under a symlinked directory, even one pointing out of the
    repo) prints by that name, not wherever the link points. A `path` containing `..`
    is never taken as written, since `..` can leave the repository: it is resolved.
    """
    root = Path(repo).resolve()
    if as_given:
        p = Path(path)
        for base in (Path(repo), root):
            # `..` is not collapsed lexically: `repo/../x` lies outside, so resolve it
            if p.is_relative_to(base) and ".." not in p.relative_to(base).parts:
                return p.relative_to(base).as_posix()
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return None if strict else str(path)


def copy_file(src: Path | str, dst: Path | str) -> None:
    """Copy the file ``src`` to ``dst``, preserving its metadata, like ``shutil.copy2``.

    The path operations (this, `copy_tree`, `move`, `rmtree`) are the `Fs` port's
    (`ddflow.adapters.fs`): callers import them here rather than ``shutil``."""
    _fs.DEFAULT.copy_file(src, dst)


def copy_tree(src: Path | str, dst: Path | str, *, dirs_exist_ok: bool = False) -> None:
    """Copy the directory tree ``src`` to ``dst``."""
    _fs.DEFAULT.copy_tree(src, dst, dirs_exist_ok=dirs_exist_ok)


def move(src: Path | str, dst: Path | str) -> None:
    """Move ``src`` to ``dst``, across filesystems if need be."""
    _fs.DEFAULT.move(src, dst)


def replace_path(src: Path | str, dst: Path | str) -> None:
    """Rename ``src`` over ``dst`` atomically (same filesystem), like ``os.replace``."""
    _fs.DEFAULT.replace_path(src, dst)


def rmtree(path: Path | str, *, ignore_errors: bool = True) -> None:
    """Remove the directory tree at ``path``, ignoring errors by default."""
    _fs.DEFAULT.rmtree(path, ignore_errors=ignore_errors)


def rmtree_owned(path: Path | str, *, ignore_errors: bool = True) -> None:
    """Remove the directory tree at ``path``, refusing the targets ``shutil.rmtree`` makes
    dangerous (AU-safe-rmtree): ``shutil.rmtree('', ignore_errors=True)`` empties the current
    directory, so an empty or relative path, the process's current directory and a filesystem
    root are each refused outright rather than left to the caller to remember."""
    p = Path(path)
    if not p.parts:
        raise ValueError("rmtree_owned: an empty path is the current directory; refused")
    if not p.is_absolute():
        raise ValueError(f"rmtree_owned: {str(p)!r} is relative; refused")
    resolved = p.resolve()
    if resolved == Path.cwd().resolve():
        raise ValueError("rmtree_owned: refusing to remove the current directory")
    if resolved == Path(resolved.anchor):
        raise ValueError("rmtree_owned: refusing to remove a filesystem root")
    rmtree(p, ignore_errors=ignore_errors)


@dataclass(frozen=True, slots=True)
class Unreadable:
    """Why `read_json` could not give a JSON object; each caller words its own refusal.

    `kind`: "invalid" (not JSON), "unreadable" (an OSError, or bytes that are not UTF-8)
    or "not-object" (valid JSON, but not an object: `value` holds it). `detail` is the
    error's own text ("" for not-object)."""

    path: Path
    kind: Literal["invalid", "unreadable", "not-object"]
    detail: str = ""
    value: Any = None


def read_json(path: Path | str) -> dict[str, Any] | Unreadable:
    """A JSON config file another tool also writes (an MCP server list, harness settings)
    as a dict: a missing or empty file is `{}`, anything else that is not a JSON object is
    an `Unreadable` saying why -- never an exception, so no reader can let a
    `UnicodeDecodeError` or a list escape as a traceback where its neighbours refuse."""
    path = Path(path)
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError:
        return {}
    except (UnicodeDecodeError, OSError) as exc:
        return Unreadable(path, "unreadable", str(exc))
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        return Unreadable(path, "invalid", str(exc))
    if not isinstance(data, dict):
        return Unreadable(path, "not-object", value=data)
    return data


class RegionError(ValueError):
    """A region's markers are duplicated, unbalanced or reversed: which lines to replace
    is ambiguous, and guessing would destroy text. Only a person can repair it."""


@dataclass(frozen=True, slots=True)
class Region:
    """The lines of a text from a line matching `begin` through a line matching `end`.

    The one line-splice primitive under every managed block: a marker is a WHOLE line
    (`re.fullmatch` per line, a trailing `\\r` ignored), so a marker quoted inside prose
    is never one. The marker grammar is the caller's (D-doc-regions); this only finds
    and replaces. `Region(None, None)` -- `APPEND` -- has no markers: it is never found,
    and splicing it appends.
    """

    begin: str | None
    end: str | None

    def find(self, text: str) -> tuple[int, int, int, int] | None:
        """`(start, body_start, body_end, stop)` offsets: the begin line starts at `start`,
        the body runs from `body_start` to `body_end` (the start of the end line), and the
        end line, with its newline, stops at `stop`. None when neither marker is present;
        RegionError when the markers are broken."""
        if self.begin is None or self.end is None:
            return None
        b, e = re.compile(self.begin), re.compile(self.end)
        begins: list[tuple[int, int]] = []
        ends: list[tuple[int, int]] = []
        at = 0
        for line in text.splitlines(keepends=True):
            bare = line.rstrip("\n").removesuffix("\r")
            is_end = e.fullmatch(bare) is not None
            # An open region's closing line is its end even when it also matches `begin`:
            # one marker line can open and close (`---` around front matter).
            if is_end and len(begins) > len(ends):
                ends.append((at, at + len(line)))
            elif b.fullmatch(bare):
                begins.append((at, at + len(line)))
            elif is_end:
                ends.append((at, at + len(line)))
            at += len(line)
        if not begins and not ends:
            return None
        if len(begins) != 1 or len(ends) != 1 or begins[0][0] > ends[0][0]:
            problem = "duplicate" if len(begins) > 1 or len(ends) > 1 else "unbalanced or reversed"
            raise RegionError(f"{problem} region markers ({self.begin!r} ... {self.end!r})")
        return begins[0][0], begins[0][1], ends[0][0], ends[0][1]

    def body(self, text: str) -> str | None:
        """The text between the marker lines, or None when the region is absent."""
        at = self.find(text)
        return None if at is None else text[at[1] : at[2]]

    def splice(self, text: str, block: str, *, gap: str = "\n") -> str:
        """`text` with the region's lines (markers included) replaced by `block`, the rest
        byte for byte. When the region is absent, `block` is appended: `text` less its
        trailing whitespace, then `gap`, then `block`. `block` is used as given -- it
        carries its own markers and final newline."""
        at = self.find(text)
        if at is None:
            return text.rstrip() + gap + block
        return text[: at[0]] + block + text[at[3] :]


#: The markerless region: splicing it appends.
APPEND = Region(None, None)


# --- Managed: the one "is this region ours, and who wrote it" interface -----------------

#: Hex characters of the body digest a managed header carries.
MANAGED_DIGEST_LEN = 12

_ATTR = re.compile(r"([A-Za-z0-9_-]+)=(\S+)")


#: A format level longer than this many digits is read as MAX_LEVEL: above any real level,
#: and never run through ``int`` (CPython refuses a very long digit string).
MAX_LEVEL_DIGITS = 9
MAX_LEVEL = 10**MAX_LEVEL_DIGITS


def parse_level(text: str) -> int | None:
    """The format level a ``fmt=`` value spells, or None when it is not a plain decimal
    number (an unknown attribute). An absurdly long number is a level above every real one."""
    if not (text.isascii() and text.isdecimal()):
        return None
    return int(text) if len(text) <= MAX_LEVEL_DIGITS else MAX_LEVEL


class NewerContent(RuntimeError):
    """A write would replace content a NEWER ddflow wrote (D-compat 2): the one refusal
    the contract allows. `needs` is the version to upgrade to (exit 3, "upgrade ddflow to
    >= X")."""

    def __init__(self, what: str, needs: str, fmt: int) -> None:
        super().__init__(
            f"{what} was written by ddflow {needs} (format level {fmt}); "
            f"upgrade ddflow to >= {needs} before rewriting it"
        )
        self.needs = needs
        self.fmt = fmt


def _digest(body: str) -> str:
    return content_digest(body, length=MANAGED_DIGEST_LEN)


@dataclass(frozen=True, slots=True)
class Stamp:
    """What a managed region's begin line says: the writer's version, its format level,
    the digest of the body it wrote, and every attribute this version does not know
    (kept, in order, when the region is rewritten at the same level)."""

    version: str
    fmt: int
    sha: str
    extra: tuple[tuple[str, str], ...] = ()


#: What `Managed.state` answers.
State = Literal["absent", "current", "older", "newer", "edited"]


@dataclass(frozen=True, slots=True)
class Managed:
    """One kind of managed region: `<open> ddflow:begin <name> ddflow=V fmt=N sha=H <close>`
    ... `<open> ddflow:end <name> <close>`, each a whole line (the D-doc-regions grammar,
    extended by version, format level and body digest).

    `owns` says whether a text holds the region, `stamp` what wrote it, `state` how it
    compares with this ddflow -- `newer` (a higher FORMAT LEVEL; a higher version alone never refuses), `older`, `edited`
    (the body no longer matches its digest, so a person changed it) or `current` -- and
    `splice` replaces it, refusing (NewerContent) only a write that would downgrade a
    newer region. The 3-way merge of an `edited` region is the caller's (D-doc-regions).
    """

    name: str
    open: str = "<!--"
    close: str = "-->"

    def _line(self, edge: str, attrs: str = "") -> str:
        tail = f" {attrs}" if attrs else ""
        return f"{self.open} ddflow:{edge} {self.name}{tail}" + (
            f" {self.close}" if self.close else ""
        )

    def _region(self) -> Region:
        o, c = re.escape(self.open), (rf" {re.escape(self.close)}" if self.close else "")
        n = re.escape(self.name)
        return Region(rf"{o} ddflow:begin {n}(?: [^\n]*?)?{c}", rf"{o} ddflow:end {n}{c}")

    def owns(self, text: str) -> bool:
        """True when the text holds this region. Raises RegionError for broken markers."""
        return self._region().find(text) is not None

    def stamp(self, text: str) -> Stamp | None:
        """The begin line's stamp, or None when the region is absent or carries none (a
        region written before stamps existed)."""
        at = self._region().find(text)
        if at is None:
            return None
        line = text[at[0] : at[1]].strip().removeprefix(self.open).removesuffix(self.close)
        attrs = dict(_ATTR.findall(line))
        if not {"ddflow", "fmt", "sha"} <= attrs.keys():
            return None
        level = parse_level(attrs["fmt"])
        if level is None:
            return None
        known = {"ddflow", "fmt", "sha"}
        extra = tuple((k, v) for k, v in _ATTR.findall(line) if k not in known)
        return Stamp(attrs["ddflow"], level, attrs["sha"], extra)

    def version(self, text: str) -> str | None:
        s = self.stamp(text)
        return None if s is None else s.version

    def state(self, text: str, *, version: str | None = None, fmt: int | None = None) -> State:
        version, fmt = version or __version__, FORMAT_LEVEL if fmt is None else fmt
        region = self._region()
        at = region.find(text)
        if at is None:
            return "absent"
        s = self.stamp(text)
        if s is None:
            return "older"
        if s.fmt > fmt:
            return "newer"
        if s.sha != _digest(text[at[1] : at[2]]):
            return "edited"
        return "older" if s.fmt < fmt or is_older(s.version, version) else "current"

    def render(
        self,
        body: str,
        *,
        version: str | None = None,
        fmt: int | None = None,
        extra: tuple[tuple[str, str], ...] = (),
    ) -> str:
        """The whole region -- begin line, body, end line -- stamped for this ddflow."""
        body = body if body.endswith("\n") or not body else body + "\n"
        attrs = [
            ("ddflow", version or __version__),
            ("fmt", str(FORMAT_LEVEL if fmt is None else fmt)),
            ("sha", _digest(body)),
            *extra,
        ]
        return (
            self._line("begin", " ".join(f"{k}={v}" for k, v in attrs))
            + "\n"
            + body
            + self._line("end")
            + "\n"
        )

    def remove(self, text: str) -> str:
        """`text` less the region (both marker lines included), the rest byte for byte;
        unchanged when it holds none. Raises RegionError for broken markers."""
        at = self._region().find(text)
        return text if at is None else text[: at[0]] + text[at[3] :]

    def splice(
        self,
        text: str,
        body: str,
        *,
        version: str | None = None,
        fmt: int | None = None,
        gap: str = "\n",
    ) -> str:
        """`text` with the region replaced by `body` stamped for this ddflow (appended when
        absent), everything outside it byte for byte. A region written at a newer FORMAT LEVEL is
        NOT rewritten: NewerContent. At the same level the attributes this version does
        not know are carried over."""
        state = self.state(text, version=version, fmt=fmt)
        s = self.stamp(text)
        if state == "newer" and s is not None:
            raise NewerContent(f"the {self.name} region", s.version, s.fmt)
        level = FORMAT_LEVEL if fmt is None else fmt
        keep = s.extra if s is not None and s.fmt == level else ()
        return self._region().splice(
            text, self.render(body, version=version, fmt=fmt, extra=keep), gap=gap
        )
