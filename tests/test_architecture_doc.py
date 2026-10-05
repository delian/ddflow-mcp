"""Every module ARCHITECTURE.md names must exist (bug B-arch-doc-stale).

`docs/ARCHITECTURE.md` is the project's least-checked artefact -- and the irony is the
document's own thesis: a rule that exists only in prose decays. It named
`services/queue.py` and a `health` module that do not exist, and its layer map never
mentioned `api/` at all, while the code (and `tests/test_layering.py`) had moved on.

A doc cannot be checked down to its prose, but it CAN be checked for the one thing it
states mechanically: the module paths it cites. Two places cite them, and a reviewer of
B-arch-doc-stale caught that the first draft of this file only covered one:

* the **layer map** -- a fenced block like `services/   queue, gates, review,` where the
  layer is on the left and the module column carries on over several lines;
* the **module map table** -- rows like `` | `services/adopt.py`, `enforce.py` | `` where
  a bare `enforce.py` inherits the layer from the path before it.

Both are parsed here: the layer is remembered across continuation lines and across the
comma-list of a table cell, so every module token is checked as `<layer>/<token>`; a
top-level line such as `config.py` is checked at the package root. (A cross-family
reviewer caught the first of these gaps -- the layer map was not parsed at all; a second
found the package-root module was not either.)
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ARCHITECTURE.md"

LAYERS = ("core", "infra", "services", "api", "views", "surfaces")

#: A contiguous `layer/name.py` reference anywhere in the document.
_REF = re.compile(rf"\b({'|'.join(LAYERS)})/([A-Za-z_]\w*)\.py\b")
#: The start of a layer-map line: `services/` at the beginning of a (stripped) line.
_LAYER_LINE = re.compile(rf"^\s*({'|'.join(LAYERS)})/\s*")
#: A bare module token: `name.py` or `name`.
_MOD_PY = re.compile(r"^[A-Za-z_]\w*\.py$")
_MOD_BARE = re.compile(r"^[A-Za-z_]\w*$")


def _tokens(text: str) -> list[str]:
    """The comma-separated module names in a map's module column. The description after
    the column is separated by two or more spaces, so it is cut off first."""
    column = re.split(r"\s{2,}", text.strip())[0]
    return [t.strip() for t in column.split(",") if t.strip()]


def _add(refs: set[str], layer: str | None, token: str) -> str | None:
    """Record `layer/token` (adding `.py` to a bare name) and return the layer to carry
    forward -- the token itself when it names one, else the unchanged layer."""
    m = re.fullmatch(rf"({'|'.join(LAYERS)})/([A-Za-z_]\w*)\.py", token)
    if m:
        refs.add(f"{m.group(1)}/{m.group(2)}.py")
        return m.group(1)
    if layer is None:
        return None
    if _MOD_PY.match(token):
        refs.add(f"{layer}/{token}")
    elif _MOD_BARE.match(token):
        refs.add(f"{layer}/{token}.py")
    return layer


def _from_layer_maps(text: str) -> tuple[set[str], set[str]]:
    """Parse every fenced layer map. A layer line sets the current layer; the indented
    lines after it are continuations of that layer's module column. A line at column 0
    that is not a layer line names a package-root module (`config.py`)."""
    refs: set[str] = set()
    roots: set[str] = set()
    for block in re.findall(r"```[^\n]*\n(.*?)```", text, re.S):
        if not _LAYER_LINE.search(block):
            continue
        layer: str | None = None
        for line in block.splitlines():
            m = _LAYER_LINE.match(line)
            if m:
                layer = m.group(1)
                column = line[m.end() :]
            elif line[:1].isspace() and line.strip():
                column = line  # a continuation row of the current layer
            elif line[:1] and not line[:1].isspace():
                # `config.py   read by every layer` -- a package-root module, no layer.
                roots.update(t for t in _tokens(line) if _MOD_PY.match(t))
                continue
            else:
                continue  # a blank line
            for token in _tokens(column):
                layer = _add(refs, layer, token)
    return refs, roots


def _from_tables(text: str) -> set[str]:
    """Parse module-map table rows: backticked tokens, a bare name inheriting the layer
    of the last path in the same cell."""
    refs: set[str] = set()
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cell = line.strip().strip("|").split("|")[0]
        layer: str | None = None
        for token in re.findall(r"`([^`]+)`", cell):
            layer = _add(refs, layer, token.strip())
    return refs


def _named_modules(text: str) -> set[str]:
    contiguous = {f"{a}/{b}.py" for a, b in _REF.findall(text)}
    layer_refs, _ = _from_layer_maps(text)
    return contiguous | layer_refs | _from_tables(text)


def _root_modules(text: str) -> set[str]:
    _, roots = _from_layer_maps(text)
    return roots


def test_every_module_named_in_the_architecture_doc_exists():
    text = DOC.read_text(encoding="utf-8")
    named = _named_modules(text)
    assert named, "ARCHITECTURE.md names no module paths -- the extraction has drifted"
    missing = sorted(ref for ref in named if not (ROOT / "ddflow" / ref).is_file())
    assert not missing, f"ARCHITECTURE.md names module paths that do not exist: {missing}"
    root_missing = sorted(n for n in _root_modules(text) if not (ROOT / "ddflow" / n).is_file())
    assert not root_missing, (
        f"ARCHITECTURE.md names top-level modules that do not exist: {root_missing}"
    )


def test_the_architecture_doc_describes_the_api_layer():
    """The `api/` package is the application layer both surfaces call; a layer map that
    omits it (as the doc once did) is the drift this file exists to catch."""
    assert "api/" in DOC.read_text(encoding="utf-8"), (
        "ARCHITECTURE.md no longer describes the api/ layer"
    )
