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
import json
import re
import sys
import tomllib
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

# The file layer lives in fsio; `atomic_write` stays importable from here for its callers.
from .fsio import atomic_write, file_lock, lock_path_for  # noqa: F401

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


def _lenient_for(path: Path, lenient: bool) -> bool:
    """``lenient``, or True for the git-ignored machine-local layer (`.ddflow/local/`),
    which any checkout's ddflow on this machine may have written -- always lenient, as
    `Config.load` treats `.ddflow/local/config.toml` (B0016a65167)."""
    p = Path(path)
    return lenient or (p.parent.name == "local" and p.parent.parent.name == ".ddflow")


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
                _check(raw, known, f"[{table}.{key}] in {path}", _lenient_for(path, lenient))
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
            where = f"[[{table}]] #{n} ({ident or 'unnamed'}) in {path}"
            spec = _check(raw, known, where, _lenient_for(path, lenient))
            if not ident:
                raise ValueError(f"[[{table}]] #{n} in {path} has no `{key}`")
            out[str(ident)] = spec
    return out


def skipped_fields(
    paths: Iterable[Path],
    table: str,
    cls: type,
    *,
    array: bool = False,
    key: str = "name",
    fallback_key: str = "",
) -> list[tuple[str, list[str]]]:
    """`(where, fields)` for each `[table.<id>]` block (or `[[table]]` entry, ``array``) in
    ``paths`` carrying fields ``cls`` does not have: what `overlay_table` / `overlay_array`
    skip (and warn about) for a file a newer ddflow wrote. Read-only and quiet, so a
    report (`ddflow doctor`) can list them for every table, not only on stderr."""
    known = set(cls.__dataclass_fields__)
    out: list[tuple[str, list[str]]] = []
    for path in paths:
        if not Path(path).is_file():
            continue
        data = tomllib.loads(Path(path).read_text("utf-8"))
        found = data.get(table)
        # The wrong container (`[[gate]]` for `[gate.x]`) is not this report's to explain:
        # the loaders say so; skip it rather than fail the report.
        if array and isinstance(found, list):
            for n, raw in enumerate(found, 1):
                if isinstance(raw, dict) and (extra := sorted(set(raw) - known)):
                    ident = raw.get(key) or (raw.get(fallback_key) if fallback_key else "")
                    out.append((f"[[{table}]] #{n} ({ident or 'unnamed'}) in {path}", extra))
        elif not array and isinstance(found, dict):
            for name, raw in found.items():
                if isinstance(raw, dict) and (extra := sorted(set(raw) - known)):
                    out.append((f"[{table}.{name}] in {path}", extra))
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
    exactly this; the config writer did not. (`fsio.file_lock`, waiting as long as it takes.)
    """
    with file_lock(lock_path_for(path)):
        yield


def basic_string(value: str) -> str:
    """`value` as a TOML basic string, escaped so any text round-trips through tomllib.

    The one writer for every hand-built TOML file (B28cab0652a, Bb11e7a8186). `json.dumps`
    escapes quotes, backslashes and control characters the way TOML does, but with its
    default `ensure_ascii` it writes a character outside the BMP (an emoji) as a UTF-16
    surrogate pair, which TOML refuses -- a `\\u` escape must be a Unicode scalar value;
    and without `ensure_ascii` it leaves U+007F raw, which TOML also refuses.

    A lone surrogate -- an undecodable filename byte or argv byte arrives as one (PEP
    383) -- has no TOML form at all, raw or escaped, so it is refused here with a
    ValueError naming it, rather than written and failing later as an encode error or
    an unreadable file.
    """
    text = str(value)
    bad = next((ch for ch in text if "\ud800" <= ch <= "\udfff"), "")
    if bad:
        raise ValueError(
            f"cannot write {text!r} to TOML: U+{ord(bad):04X} is not a Unicode character "
            f"(an undecodable byte?)"
        )
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def value(v: object) -> str:
    """Any plain value as a TOML literal: a string, bool, number, list or dict (as an
    inline table), nested. Every hand-built TOML value goes through this or
    `basic_string`: `json.dumps` agrees with TOML on strings and arrays except for
    non-BMP characters (Bb11e7a8186), disagrees on objects outright
    (B-reviewers-add-launch-json), and an f-string `"{x}"` breaks on a quote."""
    if isinstance(v, str):
        return basic_string(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_key(k)} = {value(x)}" for k, x in v.items()) + "}"
    return basic_string(str(v))


#: No leading zeros: TOML has no `01234` (a zero-padded id is text, so it is quoted).
_LITERAL = re.compile(r"(true|false|-?(0|[1-9]\d*)(\.\d+)?|\[.*\]|\{.*\})")


def literal(text: str) -> str:
    """``text`` as a TOML value: itself when it already is a boolean, a number, an array or
    an inline table (what a person types after ``--set key``), else a quoted, escaped string.
    The command-line spelling of `value`."""
    if _LITERAL.fullmatch(text.strip()):
        return text
    return basic_string(text)


def _tomlkit() -> Any:
    """The tomlkit module, imported on first use: 38 ms, and most commands never edit a config."""
    import tomlkit

    return tomlkit


def remove(text: str, dotted: str) -> tuple[str, bool]:
    """``(text without <section>.<key>, whether it was there)``, everything else as it was
    (comments, blank lines, key order). Raises ``tomllib.TOMLDecodeError`` when ``text`` is
    not TOML. A section left empty stays: the table header is the operator's."""
    tomlkit = _tomlkit()
    section, _, key = dotted.rpartition(".")
    try:
        doc = tomlkit.parse(text)
    except tomlkit.exceptions.TOMLKitError:
        tomllib.loads(text)
        raise
    table: Any = doc
    for part in [p.strip() for p in section.split(".")] if section else []:
        table = table.get(part) if isinstance(table, dict) else None
        if table is None:
            return text, False
    if not isinstance(table, dict) or key.strip() not in table:
        return text, False
    del table[key.strip()]
    return tomlkit.dumps(doc), True


def _drop_if_empty(text: str, dotted: str) -> str:
    """``text`` without the table ``dotted`` (``a`` or ``a.b``) when it is there and empty."""
    tomlkit = _tomlkit()
    doc = tomlkit.parse(text)
    parts = [p.strip() for p in dotted.split(".")]
    parent: Any = doc
    for part in parts[:-1]:
        parent = parent.get(part) if isinstance(parent, dict) else None
    table = parent.get(parts[-1]) if isinstance(parent, dict) else None
    if not isinstance(table, dict) or table:
        return text
    del parent[parts[-1]]
    return tomlkit.dumps(doc)


def move_keys(
    text: str, moves: Mapping[str, str], *, live: Iterable[str] = ()
) -> tuple[str, list[str]]:
    """``(text with each old ``<section>.<key>`` of ``moves`` moved to its new key, the old
    keys that were there)``: comments and layout kept (`remove`, `upsert`). The value moves
    as written; a new key already present stays as it is (the more recent spelling wins),
    and the old one is dropped. An old table left empty is dropped too unless it is one of
    the ``live`` section names. Raises ``tomllib.TOMLDecodeError`` when ``text`` is not TOML."""
    keep = set(live)
    moved: list[str] = []
    for old, new in moves.items():
        osec, _, okey = old.rpartition(".")
        table: Any = _tomlkit().parse(text) if osec else None
        for part in [p.strip() for p in osec.split(".")] if osec else []:
            table = table.get(part) if isinstance(table, dict) else None
        if not isinstance(table, dict) or okey not in table:
            continue
        literal_text = table[okey].as_string().strip()
        text, _ = remove(text, old)
        if osec not in keep:
            text = _drop_if_empty(text, osec)
        nsec, _, nkey = new.rpartition(".")
        have: Any = tomllib.loads(text)
        for part in nsec.split("."):
            have = have.get(part) if isinstance(have, dict) else None
        if not (isinstance(have, dict) and nkey in have):
            text = upsert(text, new, literal_text)
        moved.append(old)
    return text, moved


def upsert(text: str, dotted: str, literal_text: str) -> str:
    """``text`` with ``<section>.<key>`` set to the TOML value ``literal_text``, everything
    else as it was: comments, blank lines, key order and the spelling of every other value.

    Pure and on TEXT, so several edits compose into one write (a half-applied change after
    the third of four is rejected is a state nobody can reason about). The only place
    ddflow uses tomlkit: it parses with comments and layout kept, which a hand-built line
    editor had to approximate (multi-line strings, brackets inside values, a header with a
    trailing comment). A key already there takes the new value in place; a new key goes at
    the end of its section; a missing section is appended after a blank line. Raises
    ``tomllib.TOMLDecodeError`` when ``text`` is not TOML and ``ValueError`` when the
    section names something that is not a table."""
    tomlkit = _tomlkit()

    section, _, key = dotted.rpartition(".")
    parts = [p.strip() for p in section.split(".")] if section else []  # no dot: top level
    try:
        doc = tomlkit.parse(text)
        value = tomlkit.parse(f"v = {literal_text}")["v"]
        table: Any = doc
        for part in parts:
            if part not in table:
                if table is doc and len(doc):
                    doc.add(tomlkit.nl())
                table[part] = tomlkit.table()
            table = table[part]
            if not isinstance(table, dict):
                raise ValueError(f"{section!r} is not a table")
        table[key.strip()] = value
        out = tomlkit.dumps(doc)
    except tomlkit.exceptions.TOMLKitError:
        tomllib.loads(text)  # not TOML: raise tomllib's error, which callers already handle
        raise ValueError(f"cannot set {dotted!r} to {literal_text}") from None
    return out.rstrip() + "\n"


def _key(k: object) -> str:
    """A TOML key: bare when it may be, else quoted."""
    k = str(k)
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else basic_string(k)
