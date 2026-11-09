"""The shipped fake of the `Git` port: scripted git answers, no repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..git import Git
from . import fake


class _Run:
    """A scripted GitResult: `ok`/`text` from the script."""

    def __init__(self, ok: bool = True, text: str = "") -> None:
        self._ok = ok
        self._text = text

    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def text(self) -> str:
        return self._text


@fake
class FakeGit(Git):
    """Records every git call and answers a scripted queue: `{"run": (ok, text)}`,
    `{"paths": [...]}`, `{"unmerged": [...]}`."""

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.script: list[dict[str, Any]] = list(script or [])

    def _next(self) -> dict[str, Any]:
        return self.script.pop(0) if self.script else {}

    def run(self, repo: Path | str, *args: str, **kwargs: Any) -> Any:
        self.calls.append(("run", (repo, *args)))
        r = self._next().get("run", (True, ""))
        return _Run(r[0], r[1])

    def paths(self, repo: Path | str, *args: str, **kwargs: Any) -> list[str] | None:
        self.calls.append(("paths", (repo, *args)))
        return self._next().get("paths")

    def unmerged(self, repo: Path | str, timeout: float | None = None) -> list[str] | None:
        self.calls.append(("unmerged", (repo,)))
        return self._next().get("unmerged")
