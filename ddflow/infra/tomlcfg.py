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

The policy, in one place: **an unknown field is an error**, and the message names the
file and the entry, because a config error whose location you have to guess is a config
error you work around -- **except** when the caller says the file may be newer than the
code (``lenient``): an older ddflow reading a newer checkout's config then warns and skips
the field, as `Config.load` does for config.toml (B9cb7dd1c3b). Refusing there made every
new reviewer knob break every older clone sharing the config (B6f757e18cf).

Two shapes, because TOML has two: a **table of tables** (`[gate.unit_tests]`) keyed by
its table name, and an **array of tables** (`[[reviewer]]`) keyed by a field inside it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import tempfile
import tomllib
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

#: (where, field) already warned about in this process: a loader runs several times per
#: command, and the same warning repeated reads as several problems.
_WARNED: set[tuple[str, str]] = set()


def _check(spec: dict[str, Any], known: set[str], where: str, lenient: bool = False) -> dict:
    """``spec`` without its unknown fields, or ValueError naming them (not ``lenient``)."""
    unknown = sorted(set(spec) - known)
    if not unknown:
        return spec
    if not lenient:
        raise ValueError(f"unknown field(s) {unknown} in {where}. Known: {sorted(known)}")
    new = [k for k in unknown if (where, k) not in _WARNED]
    if new:
        _WARNED.update((where, k) for k in new)
        print(
            f"ddflow: warning: {where} sets {', '.join(new)}, which this ddflow does not "
            f"know; skipped. The config is newer than this code: merge main into this tree "
            f"(or, if it is a typo, fix it).",
            file=sys.stderr,
        )
    return {k: v for k, v in spec.items() if k in known}


def overlay_table(
    paths: Iterable[Path], table: str, cls: type, *, lenient: bool = False
) -> dict[str, dict[str, Any]]:
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
        for key, raw in (data.get(table) or {}).items():
            if not isinstance(raw, dict):
                continue
            out.setdefault(key, {}).update(
                _check(raw, known, f"[{table}.{key}] in {path}", lenient)
            )
    return out


def overlay_array(
    paths: Iterable[Path],
    table: str,
    cls: type,
    *,
    key: str,
    fallback_key: str = "",
    lenient: bool = False,
) -> dict[str, dict[str, Any]]:
    """`[[table]]` blocks, merged by the value of ``key``. Later paths win."""
    known = set(cls.__dataclass_fields__)
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not Path(path).is_file():
            continue
        data = tomllib.loads(Path(path).read_text("utf-8"))
        for n, raw in enumerate(data.get(table) or [], 1):
            if not isinstance(raw, dict):
                continue
            ident = raw.get(key) or (raw.get(fallback_key) if fallback_key else "")
            spec = _check(raw, known, f"[[{table}]] #{n} ({ident or 'unnamed'}) in {path}", lenient)
            if not ident:
                raise ValueError(f"[[{table}]] #{n} in {path} has no `{key}`")
            out[str(ident)] = spec
    return out


def config_paths(root: Path, own: str) -> tuple[Path, ...]:
    """The files every configurable surface here reads, in precedence order (later wins).

    `.ddflow/config.toml` first so a project has ONE obvious place to configure, then
    the dedicated file for operators who prefer to split it out, then the same two under
    the git-ignored `.ddflow/local/` for what belongs to this machine alone. A file that had to
    restate everything to change one thing gets copied once and then drifts, which is
    the failure this whole module is about.
    """
    base = Path(root) / ".ddflow"
    # The machine-local layer last, so it wins: .ddflow/local/ is git-ignored, and holds
    # what belongs to whoever runs this checkout (their reviewers, their companions).
    return (base / "config.toml", base / own, base / "local" / "config.toml", base / "local" / own)


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


def basic_string(value: str) -> str:
    """`value` as a TOML basic string, escaped so any text round-trips through tomllib.

    The one writer for every hand-built TOML file (B28cab0652a, Bb11e7a8186). `json.dumps`
    escapes quotes, backslashes and control characters the way TOML does, but with its
    default `ensure_ascii` it writes a character outside the BMP (an emoji) as a UTF-16
    surrogate pair, which TOML refuses -- a `\\u` escape must be a Unicode scalar value;
    and without `ensure_ascii` it leaves U+007F raw, which TOML also refuses.
    """
    return json.dumps(str(value), ensure_ascii=False).replace("\x7f", "\\u007f")


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
