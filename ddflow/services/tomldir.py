"""One loader for a directory of `<id>.toml` definitions (schedules, triggers).

Each file is one definition whose id is the file name. A file that does not parse, names a
different id, or does not validate is reported as `<dir>/<file>: <why>` and left out; it
never stops the rest.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any


def load_toml_dir(
    repo: Path,
    rel_dir: Path,
    build: Callable[[str, dict[str, Any]], tuple[Any, list[str]]],
    mismatch: Callable[[Any], str],
) -> tuple[list[tuple[str, Any, str]], list[str]]:
    """([(id, built, repo-relative path)], errors) for every `repo/rel_dir/*.toml`, in file-name order.

    `build(id, spec)` returns (the definition or None, the reasons it is None); `mismatch(
    declared_id)` is the message for a file whose own `id` differs from its name."""
    d = Path(repo) / rel_dir
    out: list[tuple[str, Any, str]] = []
    errors: list[str] = []
    if not d.is_dir():
        return out, errors
    for f in sorted(d.glob("*.toml")):
        rel = (rel_dir / f.name).as_posix()
        try:
            spec = tomllib.loads(f.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"{rel}: cannot read it: {exc}")
            continue
        declared = spec.pop("id", f.stem)
        if declared != f.stem:
            errors.append(f"{rel}: {mismatch(declared)}")
            continue
        built, bad = build(f.stem, spec)
        if built is None:
            errors.append(f"{rel}: " + "; ".join(bad))
            continue
        out.append((f.stem, built, rel))
    return out, errors
