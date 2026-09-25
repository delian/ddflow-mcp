"""One TOML overlay loader, and one unknown-key policy.

Three copies of this existed — `gates.load_gates`, `reviewer.load_reviewers` and
`companions.load` — all with the same shape: walk a tuple of paths, skip the missing
ones, parse, filter against a dataclass's fields, merge by id with the later file
winning. They had already drifted three ways, which is what made a shared helper worth
extracting rather than merely tidy:

* **Unknown keys.** Gates raised, reviewers raised, **companions silently dropped**. A
  misspelt `commmand` produced a companion with an empty command that `companions add`
  would write into an agent's MCP config as a launch line failing mid-task. That is the
  silent-knob-drop class, inside a package whose own config loader raises on a typo'd
  *section* specifically to prevent it.
* **Which files.** Gates and reviewers read `config.toml` *and* their dedicated file;
  companions read only its own. `load_gates`' docstring records this exact bug being
  found and fixed for gates — "a config write that silently does nothing is worse than
  one that errors" — and companions was written afterwards with the old shape.
* **Where `known` was computed.** Hoisted in two, recomputed in the innermost loop in
  the third: harmless, and a tell that these were copied at different times.

The policy, in one place: **an unknown field is always an error**, and the message names
the file and the entry, because a config error whose location you have to guess is a
config error you work around.

Two shapes, because TOML has two: a **table of tables** (`[gate.unit_tests]`) keyed by
its table name, and an **array of tables** (`[[reviewer]]`) keyed by a field inside it.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile
import tomllib
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


def _check(spec: dict[str, Any], known: set[str], where: str) -> None:
    unknown = sorted(set(spec) - known)
    if unknown:
        raise ValueError(f"unknown field(s) {unknown} in {where}. Known: {sorted(known)}")


def overlay_table(paths: Iterable[Path], table: str, cls: type) -> dict[str, dict[str, Any]]:
    """`[table.<id>]` blocks, merged by id. Later paths win. Returns raw dicts.

    Raw rather than constructed, because the callers differ in what they do next: gates
    overlay onto shipped defaults, so they need the fields that were *given* rather than
    a fully-populated object.
    """
    known = set(cls.__dataclass_fields__)
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not Path(path).is_file():
            continue
        data = tomllib.loads(Path(path).read_text("utf-8"))
        for key, spec in (data.get(table) or {}).items():
            if not isinstance(spec, dict):
                continue
            _check(spec, known, f"[{table}.{key}] in {path}")
            out.setdefault(key, {}).update(spec)
    return out


def overlay_array(
    paths: Iterable[Path], table: str, cls: type, *, key: str, fallback_key: str = ""
) -> dict[str, dict[str, Any]]:
    """`[[table]]` blocks, merged by the value of ``key``. Later paths win."""
    known = set(cls.__dataclass_fields__)
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not Path(path).is_file():
            continue
        data = tomllib.loads(Path(path).read_text("utf-8"))
        for n, spec in enumerate(data.get(table) or [], 1):
            if not isinstance(spec, dict):
                continue
            ident = spec.get(key) or (spec.get(fallback_key) if fallback_key else "")
            _check(spec, known, f"[[{table}]] #{n} ({ident or 'unnamed'}) in {path}")
            if not ident:
                raise ValueError(f"[[{table}]] #{n} in {path} has no `{key}`")
            out[str(ident)] = spec
    return out


def config_paths(root: Path, own: str) -> tuple[Path, Path]:
    """The two files every configurable surface here reads, in precedence order.

    `.ddflow/config.toml` first so a project has ONE obvious place to configure, then
    the dedicated file for operators who prefer to split it out. A file that had to
    restate everything to change one thing gets copied once and then drifts, which is
    the failure this whole module is about.
    """
    return (Path(root) / ".ddflow" / "config.toml", Path(root) / ".ddflow" / own)


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on `<path>.lock` for a read-modify-write.

    Every config writer here is read-modify-write, and this package's whole purpose is
    several agents working at once -- so two of them calling `ddflow workflow gate ...`
    is the normal case, not an exotic one. Unlocked, 40 concurrent pairs lost half their
    edits and every call returned success. The event log has always taken a lock for
    exactly this; the config writer did not.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(f".{path.name}.lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def atomic_write(path: Path, text: str) -> None:
    """Write via a UNIQUE temp file in the same directory, then one `os.replace`.

    A plain `write_text` truncates first: interrupted between truncate and write -- a
    crash, a full disk, a killed agent -- it leaves an EMPTY config, which loads as "no
    overrides at all" rather than as an error. Every knob silently reverts to its
    default and nothing says so, which is the failure mode this module exists to
    prevent one layer up.

    The temp name is unique, and that is not fussiness. A FIXED name is shared: two
    writers raced, one `os.replace`d the other's half-written file into place, and a
    watcher caught `config.toml` TORN at 118 KB of a 400 KB write -- the truncated
    config this function was written to make impossible. The `finally` unlink also
    deleted whichever tmp existed, including the other writer's in flight, so 199 of
    400 calls died with `FileNotFoundError` out of `os.replace`.

    Same directory, so the rename stays within one filesystem and is therefore atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Only on failure. Unconditionally is what let one writer delete another's.
        tmp.unlink(missing_ok=True)
        raise
