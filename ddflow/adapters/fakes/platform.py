"""The shipped fake of the `Platform` port: recorded answers, no OS differences."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import fake


@fake
class FakePlatform:
    """A `Platform` that locks are a recorded no-op on, and whose answers are scripted.

    ``which`` returns a value only for the names given it; ``is_terminal`` reports the
    stream's own ``isatty()`` when it has one, else False; ``chmod_private`` records the
    path; ``home_dir`` returns the given ``home``.
    """

    def __init__(
        self, home: Path | str = "/home/fake", which_of: dict[str, str] | None = None
    ) -> None:
        self.home = Path(home)
        self.which_of = dict(which_of or {})
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def file_lock(self, path: Path | str, **kwargs: Any) -> Iterator[None]:
        self.calls.append(("file_lock", (str(path),)))

        @contextlib.contextmanager
        def _held() -> Iterator[None]:
            yield

        return _held()

    def kill_group(self, proc: Any, sig: int | None = None) -> None:
        self.calls.append(("kill_group", (proc, sig)))

    def which(self, cmd: str) -> str | None:
        self.calls.append(("which", (cmd,)))
        return self.which_of.get(cmd)

    def home_dir(self) -> Path:
        self.calls.append(("home_dir", ()))
        return self.home

    def chmod_private(self, path: Path | str) -> None:
        self.calls.append(("chmod_private", (str(path),)))

    def is_terminal(self, stream: Any) -> bool:
        self.calls.append(("is_terminal", (stream,)))
        return bool(stream is not None and hasattr(stream, "isatty") and stream.isatty())
