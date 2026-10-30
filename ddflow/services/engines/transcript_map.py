"""E7 transcript_map: a flat JSONL transcript to ``role / text / usage / model / ts``.

Flat means one JSON object per line and plain-string fields reached by dotted path; trees and
compressed stores stay with a driver. The path template has a closed placeholder list, and a
session id from the agent can never leave the directory the template names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..hookio import _dig
from .safety import DEFAULT, EngineError, Limits, check_path, expect_keys, finite_float, no_constant

MAX_PATH_CHARS = 4096  #: a longer template or resolved path is refused before it is split
FIELDS = ("role", "text", "usage", "model", "ts")
PLACEHOLDERS = ("home", "dir", "session")
_PH = re.compile(r"\{([a-z_]+)\}")
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True)
class Spec:
    path: str
    fields: dict[str, str]


@dataclass(frozen=True)
class Entry:
    role: str = ""
    text: str = ""
    usage: int | None = None
    model: str = ""
    ts: str = ""


def parse(table: object, limits: Limits = DEFAULT) -> Spec:
    t = expect_keys(table, {"path", "format", "fields"}, "transcript")
    if t.get("format") != "jsonl":
        raise EngineError("transcript.format: only 'jsonl' (flat) is an engine; others are drivers")
    path = t.get("path")
    if not isinstance(path, str) or not path or "\0" in path or len(path) > MAX_PATH_CHARS:
        raise EngineError("transcript.path: a non-empty template")
    if ".." in re.split(r"[\\/:]", path):
        raise EngineError("transcript.path: '..' is not allowed")
    for name in _PH.findall(path):
        if name not in PLACEHOLDERS:
            raise EngineError(
                f"transcript.path: unknown placeholder {{{name}}}; closed: {list(PLACEHOLDERS)}"
            )
    if re.sub(_PH, "", path).count("{") or re.sub(_PH, "", path).count("}"):
        raise EngineError("transcript.path: malformed placeholder")
    fields = expect_keys(t.get("fields"), set(FIELDS), "transcript.fields")
    if not fields:
        raise EngineError("transcript.fields: name at least one field")
    return Spec(
        path, {k: check_path(v, f"transcript.fields.{k}", limits) for k, v in fields.items()}
    )


def resolve_path(spec: Spec, *, home: str = "", dir: str = "", session: str = "") -> str:
    """The transcript path; ``session`` must be one safe path segment, ``home``/``dir`` absolute."""
    vals = {"home": home, "dir": dir, "session": session}
    if not all(isinstance(v, str) for v in vals.values()):
        raise EngineError("home, dir and session are strings")
    used = set(_PH.findall(spec.path))
    if ("session" in used and not _SEGMENT.fullmatch(session or "")) or session in (".", ".."):
        raise EngineError("session id is not a single safe path segment")
    for k in ("home", "dir"):
        if k in used and (
            not vals[k].startswith("/") or "\0" in vals[k] or ".." in re.split(r"[\\/:]", vals[k])
        ):
            raise EngineError(f"{k} must be an absolute path without '..'")
    out = _PH.sub(lambda m: vals[m.group(1)], spec.path)
    if len(out) > MAX_PATH_CHARS:
        raise EngineError("the resolved transcript path is too long")
    if ".." in re.split(r"[\\/:]", out):
        raise EngineError("the resolved transcript path has a '..' segment")
    return out


def _s(g: dict, key: str, limits: Limits) -> str:
    v = g.get(key)
    return v[: limits.max_value_chars] if isinstance(v, str) else ""


def map_lines(lines, spec: Spec, limits: Limits = DEFAULT) -> list[Entry]:
    out: list[Entry] = []
    for line in lines:
        if len(line) > limits.max_line_chars or not line.strip():
            continue
        try:
            raw = json.loads(line, parse_constant=no_constant, parse_float=finite_float)
        except (ValueError, RecursionError):
            continue
        if not isinstance(raw, dict):
            continue
        g = {k: _dig(raw, p) for k, p in spec.fields.items()}
        usage = g.get("usage")
        out.append(
            Entry(
                _s(g, "role", limits),
                _s(g, "text", limits),
                usage if type(usage) is int else None,
                _s(g, "model", limits),
                _s(g, "ts", limits),
            )
        )
    return out
