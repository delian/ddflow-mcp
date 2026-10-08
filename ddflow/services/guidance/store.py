"""A kind's authored files on disk: ``.ddflow/<directory>/<id>.toml``, one record each.

The directory goes through the overlay loader (`services.overlay`): the project's files are
the layer a person edits, a shipped pack (``ddflow/templates/guidance/<directory>/``) is the
layer beneath, and `problems` reports what does not parse as ``file:line`` the way every other
overlay does. Reading and writing a file is `fileformat`'s; this only finds, lists and
replaces files.
"""

from __future__ import annotations

import re
from pathlib import Path

from ...infra.fsio import replace_text
from ..overlay import OverlayError, OverlayLoader, Problem, SyntaxProblem
from . import fileformat
from .record import GuidanceRecord, KindSpec

_SHIPPED = Path(__file__).resolve().parents[2] / "templates" / "guidance"
_AT_LINE = re.compile(r"\(at line (\d+)")


def _checker(spec: KindSpec):
    """The overlay's syntax check for the kind: the file must parse as guidance of the kind."""

    def check(text: str) -> None:
        try:
            fileformat.parse(text, spec)
        except ValueError as exc:
            m = _AT_LINE.search(str(exc))
            raise SyntaxProblem(int(m.group(1)) if m else 0, str(exc)) from exc

    return check


class GuidanceFiles:
    """The files of one kind in one repository."""

    def __init__(self, repo: Path, spec: KindSpec) -> None:
        self.repo = Path(repo)
        self.spec = spec
        self.loader = OverlayLoader(
            spec.kind,
            shipped_dir=_SHIPPED / spec.directory,
            project_subdir=spec.directory,
            suffix=".toml",
            syntax="toml",
            check=_checker(spec),
        )
        self.directory = self.loader.project_dir(self.repo)

    def ensure_dir(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def path(self, rid: str) -> Path:
        return self.loader.project_path(self.repo, rid)

    def exists(self, rid: str) -> bool:
        """Whether a record has a file: the project's own, or a shipped one beneath it."""
        return self.path(rid).is_file() or self.loader.shipped_path(rid).is_file()

    def read(self, rid: str) -> GuidanceRecord:
        """The record in ``<id>.toml``; FileNotFoundError when the file does not exist."""
        if not self.exists(rid):
            raise FileNotFoundError(f"{self.spec.label} {rid} not found at {self.path(rid)}")
        return fileformat.parse(self.loader.resolve(rid, self.repo).text, self.spec)

    def write(self, rec: GuidanceRecord, *, new: bool = False) -> None:
        """Write ``rec`` to its file; ``new`` refuses to replace one that exists."""
        self.ensure_dir()
        path = self.path(rec.id)
        if new and (path.exists() or self.exists(rec.id)):
            raise ValueError(f"{self.spec.label} {rec.id} already exists at {path}")
        replace_text(path, fileformat.render(rec, self.spec))

    def delete(self, rid: str) -> None:
        path = self.path(rid)
        if not path.exists():
            raise ValueError(f"{self.spec.label} {rid} not found at {path}")
        path.unlink()

    def names(self) -> list[str]:
        """The ids with a file, in file-name order."""
        suffix = self.loader.suffix
        return sorted(self.loader.names(self.repo), key=lambda n: n + suffix)

    def all(self) -> list[GuidanceRecord]:
        """Every record that loads, in file-name order; a file that does not is left out."""
        self.ensure_dir()
        out = []
        for name in self.names():
            try:
                out.append(self.read(name))
            except (OSError, OverlayError, ValueError):
                continue
        return out

    def problems(self) -> list[Problem]:
        """Every file that is not valid guidance of the kind, as ``file:line: why``."""
        found: list[Problem] = []
        for name in self.names():
            try:
                found += self.loader.validate(self.loader.resolve(name, self.repo))
            except OverlayError as exc:
                found.append(Problem(self.path(name), 0, str(exc)))
        return found
