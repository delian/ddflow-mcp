"""[[docs.extract]] for the documentation check: the public surface the code offers, and
which of it no checked document mentions (decision D-docs-stage).

An extractor reads tracked source files and yields names -- command-line flags, tool names,
config knobs -- by a regular expression the project wrote. Each name that no document
mentions becomes a finding of the kind a document's reference to it would have: flags are
``flag``, tools ``command``, knobs ``identifier``. ``surface`` is public so the check command
can keep only the names a diff added.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .docscheck_types import Finding
from .docsync import glob_regex

#: Larger source files are not read, as the corpus does not read them.
MAX_SOURCE_BYTES = 1_000_000
#: The finding kind for each extractor kind.
FINDING_KIND = {"flags": "flag", "tools": "command", "knobs": "identifier"}

_FLAG_TOKEN = re.compile(r"--[A-Za-z0-9][A-Za-z0-9_-]*")
_WORD_TOKEN = re.compile(r"\w+(?:[-.]\w+)*")
_QUALIFIED = re.compile(r"\[(\w+)\]\.")  # `[docs].extract` is the knob docs.extract


@dataclass(frozen=True)
class Extractor:
    kind: str
    pattern: re.Pattern[str]
    source_globs: tuple[str, ...]

    @classmethod
    def from_config(cls, entry: Mapping) -> Extractor:
        kind = entry.get("kind")
        if kind not in FINDING_KIND:
            raise ValueError(f"[[docs.extract]] kind {kind!r} is not one of {sorted(FINDING_KIND)}")
        globs = entry.get("source_globs")
        if not isinstance(globs, list | tuple) or not globs:
            raise ValueError("[[docs.extract]] needs a non-empty source_globs")
        try:
            rx = re.compile(str(entry.get("pattern", "")), re.M)
        except re.error as exc:
            raise ValueError(f"[[docs.extract]] pattern: {exc}") from exc
        if rx.groups < 1:
            raise ValueError("[[docs.extract]] pattern needs a capture group (the name)")
        return cls(kind, rx, tuple(globs))


@dataclass(frozen=True)
class Surface:
    kind: str  #: flags | tools | knobs
    name: str
    path: str  #: the source file
    line: int


def surface(root, files: Iterable[str], extractors: Iterable[Extractor]) -> list[Surface]:
    """Every name the extractors find in the tracked ``files``, first sighting of each."""
    found: dict[tuple[str, str], Surface] = {}
    for ex in extractors:
        rx = [glob_regex(g) for g in ex.source_globs]
        for path in sorted(p for p in files if any(r.fullmatch(p) for r in rx)):
            text = _read(Path(root) / path)
            if text is None:
                continue
            for m in ex.pattern.finditer(text):
                name = (m.group("name") if "name" in ex.pattern.groupindex else m.group(1)) or ""
                if name and (ex.kind, name) not in found:
                    line = text.count("\n", 0, m.start()) + 1
                    found[(ex.kind, name)] = Surface(ex.kind, name, path, line)
    return list(found.values())


def _read(path: Path) -> str | None:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_SOURCE_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_SOURCE_BYTES or b"\0" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def undocumented(
    names: Iterable[Surface], doc_texts: Iterable[str], owned=lambda name: False
) -> list[Finding]:
    """The names no document in ``doc_texts`` mentions. ``owned(name)`` is true for a name
    another tool owns (``ignore_names``): never reported."""
    flags: set[str] = set()
    words: set[str] = set()
    spaced: list[str] = []  #: whitespace-normalised text, for a name of several words
    for text in doc_texts:
        flags.update(_FLAG_TOKEN.findall(text))
        words.update(_WORD_TOKEN.findall(_QUALIFIED.sub(r"\1.", text)))
        spaced.append(" ".join(text.split()))
    blob = "\n".join(spaced)
    out = []
    for s in names:
        if owned(s.name) or _mentioned(s, flags, words, blob):
            continue
        out.append(
            Finding(
                FINDING_KIND[s.kind],
                s.path,
                s.line,
                s.name,
                f"extracted as {s.kind[:-1]} surface; no checked document mentions it",
            )
        )
    return out


def _mentioned(s: Surface, flags: set[str], words: set[str], blob: str) -> bool:
    if s.kind == "flags":
        return s.name in flags
    if s.name in words:
        return True
    name = " ".join(s.name.split())
    return " " in name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", blob) is not None
