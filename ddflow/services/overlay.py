"""One overlay loader for every shipped asset (D-unify, B-uni-overlay).

A shipped text (a prompt, an export template, a doctype, a schedule ...) can be replaced by
the project without touching code. The resolution order is the same everywhere, first hit
wins, and a hit says which layer it came from:

1. ``config``  -- an explicit path the operator configured; one that does not exist is an
   error, never a silent fallback to the default;
2. ``project`` -- ``<repo>/.ddflow/<project_subdir>/<name><suffix>``;
3. ``builtin`` -- the file shipped in the package.

``eject`` copies the shipped text into the project behind a one-line marker recording the
digest of the shipped text it came from (``{# ddflow-shipped: 3f2a9c1b04de -#}`` for a Jinja
text, ``# ddflow-shipped: 3f2a9c1b04de`` for a TOML one). With it ddflow can tell an UNEDITED
copy (its body still hashes to the recorded digest: refreshed freely) from an EDITED one
(never overwritten without ``force``), and a copy older than the shipped default from a
current one (``drift``). A ddflow upgrade never rewrites a copy. ``validate`` parses a text
with the kind's parser and names the first problem as ``file:line``.

Pure of any one kind: the callers (`services.prompts`, `services.export.templates`, ...) move
onto this one slice at a time, each byte-identical, and then the planned loaders are built on
it instead of copying it.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jinja2

from ..core.digest import content_digest
from ..infra import tomlcfg

BUILTIN = "builtin"
PROJECT = "project"
CONFIG = "config"

#: Marker line per text syntax: a template for the line, and the pattern that reads it back.
_MARKERS = {
    "jinja": (
        "{{# ddflow-shipped: {digest} -#}}\n",
        re.compile(r"\A\{#\s*ddflow-shipped:\s*([0-9a-f]{12})\s*-#\}\n?"),
    ),
    "toml": (
        "# ddflow-shipped: {digest}\n",
        re.compile(r"\A#\s*ddflow-shipped:\s*([0-9a-f]{12})[ \t]*\n?"),
    ),
}


class OverlayError(RuntimeError):
    """A missing, unreadable or refused asset; the message is the whole story."""

    def __init__(self, message: str, *, refused: bool = False) -> None:
        super().__init__(message)
        #: True for a refusal the operator can lift (an edited copy), False for "could not".
        self.refused = refused


@dataclass(frozen=True)
class Asset:
    name: str
    text: str
    source: str  # config | project | builtin
    path: Path | None


@dataclass(frozen=True)
class Ejection:
    name: str
    path: Path
    action: str  # created | updated | overwritten | unchanged


@dataclass(frozen=True)
class Drift:
    name: str
    path: Path
    edited: bool  # the copy has the project's own edits
    recorded: str  # the shipped digest the copy was made from


@dataclass(frozen=True)
class Problem:
    path: Path
    line: int  # 0 when the parser did not say
    message: str

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.line}: {self.message}"
            if self.line
            else f"{self.path}: {self.message}"
        )


def digest(text: str) -> str:
    """12 hex chars of sha256 over a text: what drift detection compares."""
    return content_digest(text, length=12)


def check_jinja(text: str) -> None:
    """Raise `SyntaxProblem` when ``text`` is not a valid Jinja2 template."""
    try:
        jinja2.Environment().parse(text)
    except jinja2.TemplateSyntaxError as exc:
        raise SyntaxProblem(exc.lineno or 0, exc.message or str(exc)) from exc


def check_toml(text: str) -> None:
    """Raise `SyntaxProblem` when ``text`` is not valid TOML."""
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        m = re.search(r"\(at line (\d+)", str(exc))
        raise SyntaxProblem(int(m.group(1)) if m else 0, str(exc)) from exc


class SyntaxProblem(ValueError):
    def __init__(self, line: int, message: str) -> None:
        super().__init__(message)
        self.line = line


class OverlayLoader:
    """The overlay of one kind of asset.

    ``kind`` names it in messages; ``shipped_dir`` is the package directory; ``project_subdir``
    is under ``<repo>/.ddflow/``; ``syntax`` is ``"jinja"`` or ``"toml"`` (the marker and the
    default parser).
    """

    def __init__(
        self,
        kind: str,
        *,
        shipped_dir: Path,
        project_subdir: str,
        suffix: str,
        syntax: str = "jinja",
        check: Callable[[str], None] | None = None,
    ) -> None:
        if syntax not in _MARKERS:
            raise ValueError(f"unknown syntax {syntax!r}; one of {', '.join(_MARKERS)}")
        self.kind = kind
        self.shipped_dir = Path(shipped_dir)
        self.project_subdir = project_subdir
        self.suffix = suffix
        self.syntax = syntax
        self._check = check or (check_jinja if syntax == "jinja" else check_toml)

    # -- where ------------------------------------------------------------------------

    def shipped_path(self, name: str) -> Path:
        return self.shipped_dir / f"{name}{self.suffix}"

    def project_dir(self, repo: Path) -> Path:
        return Path(repo) / ".ddflow" / self.project_subdir

    def project_path(self, repo: Path, name: str) -> Path:
        return self.project_dir(repo) / f"{name}{self.suffix}"

    def names(self, repo: Path | None = None) -> list[str]:
        """Every asset name: the shipped ones, then the project's own, each once, sorted."""
        found: set[str] = set()
        dirs = [self.shipped_dir, *([self.project_dir(repo)] if repo else [])]
        for d in dirs:
            if d.is_dir():
                found.update(
                    p.name[: -len(self.suffix)] for p in d.glob(f"*{self.suffix}") if p.is_file()
                )
        return sorted(found)

    # -- resolve ----------------------------------------------------------------------

    def shipped(self, name: str) -> Asset:
        path = self.shipped_path(name)
        if not path.is_file():
            raise OverlayError(f"shipped {self.kind} {path.name} is missing from the package")
        return Asset(name, _read(path), BUILTIN, path)

    def resolve(self, name: str, repo: Path | None = None, configured: str = "") -> Asset:
        """The asset ``name``: the configured path, else the project's copy, else the shipped one."""
        if configured:
            path = Path(configured)
            if not path.is_absolute() and repo:
                path = Path(repo) / path
            if not path.is_file():
                raise OverlayError(
                    f"the configured {self.kind} for {name} is {path}, which does not exist"
                )
            return Asset(name, _read(path), CONFIG, path)
        if repo:
            path = self.project_path(repo, name)
            if path.is_file():
                return Asset(name, _read(path), PROJECT, path)
        return self.shipped(name)

    # -- the marker -------------------------------------------------------------------

    def split_mark(self, text: str) -> tuple[str, str]:
        """``(recorded shipped digest or "", the text without the marker line)``."""
        m = _MARKERS[self.syntax][1].match(text)
        return (m.group(1), text[m.end() :]) if m else ("", text)

    def ejected_text(self, name: str) -> str:
        """What eject writes for ``name`` today."""
        shipped = self.shipped(name).text
        return _MARKERS[self.syntax][0].format(digest=digest(shipped)) + shipped

    def is_edited(self, text: str, name: str) -> bool:
        """True when ``text`` (an existing copy) is neither a pristine copy of some shipped
        version (recorded digest = digest of its own body) nor the current shipped text."""
        recorded, body = self.split_mark(text)
        if recorded:
            return digest(body) != recorded
        return body != self.shipped(name).text

    # -- eject ------------------------------------------------------------------------

    def eject(self, repo: Path, name: str, *, force: bool = False) -> Ejection:
        """Copy the shipped asset into the project. Idempotent; an unedited older copy is
        refreshed; an edited one is refused without ``force``; a symlink is never written through."""
        repo = Path(repo)
        dst = self.project_path(repo, name)
        base = repo / ".ddflow"
        # every component from .ddflow down: a nested project_subdir has intermediate directories
        for p in (base, *(base / q for q in _prefixes(dst.relative_to(base)))):
            if p.is_symlink():
                raise OverlayError(
                    f"{p} is a symlink; ddflow does not write through it", refused=True
                )
        new = self.ejected_text(name)
        old = _read_or_none(dst)
        action = "created"
        if old is not None:
            if old == new:
                action = "unchanged"
            elif not self.is_edited(old, name):
                action = "updated"
            elif force:
                action = "overwritten"
            else:
                raise OverlayError(
                    f"{dst} was edited since it was ejected; refusing to overwrite it "
                    "(force replaces it with the shipped default, losing your edits)",
                    refused=True,
                )
        if action != "unchanged":
            dst.parent.mkdir(parents=True, exist_ok=True)
            tomlcfg.atomic_write(dst, new)
        return Ejection(name, dst, action)

    # -- drift and validate -----------------------------------------------------------

    def drift(self, repo: Path, name: str) -> Drift | None:
        """The project's copy of ``name`` when it was ejected from an older shipped text,
        else None (no copy, no marker, or current). Never raises."""
        path = self.project_path(repo, name)
        try:
            text = _read_or_none(path)
            if text is None:
                return None
            recorded, body = self.split_mark(text)
            if recorded and recorded != digest(self.shipped(name).text):
                return Drift(name, path, digest(body) != recorded, recorded)
        except OverlayError:
            return None
        return None

    def validate(self, asset: Asset) -> list[Problem]:
        """Parse ``asset`` with the kind's parser: [] when it parses, else one `Problem`."""
        path = asset.path or Path(asset.name)
        try:
            self._check(asset.text)  # the marker is a comment, so line numbers match the file
        except SyntaxProblem as exc:
            return [Problem(path, exc.line, str(exc))]
        return []


def _prefixes(rel: Path) -> list[Path]:
    """``a/b/c`` -> ``a``, ``a/b``, ``a/b/c``."""
    parts = rel.parts
    return [Path(*parts[: i + 1]) for i in range(len(parts))]


def _read(p: Path) -> str:
    try:
        return p.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OverlayError(f"could not read {p}: {exc}") from exc


def _read_or_none(p: Path) -> str | None:
    try:
        return p.read_text("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise OverlayError(f"could not read {p}: {exc}") from exc
