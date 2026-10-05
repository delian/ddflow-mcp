"""Every module ARCHITECTURE.md names must exist (bug B-arch-doc-stale).

`docs/ARCHITECTURE.md` is the project's least-checked artefact -- and the irony is the
document's own thesis: a rule that exists only in prose decays. It named
`services/queue.py` and a `health` module that do not exist, and its layer list never
mentioned `api/` at all, while the code (and `tests/test_layering.py`) had moved on.

A doc cannot be checked down to its prose, but it CAN be checked for the one thing it
states mechanically: the module paths it cites. This walks every `layer/name.py`
reference in the document and requires the file to be in the tree, so the same drift
fails the suite instead of waiting for a reader to notice.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ARCHITECTURE.md"

#: A `layer/name.py` reference, where layer is one of the package's layers. `config.py`
#: sits at the top level with no layer prefix, so it is deliberately not matched here.
_REF = re.compile(r"\b(core|infra|services|api|views|surfaces)/([A-Za-z_][A-Za-z0-9_]*)\.py\b")


def _named_modules(text: str) -> list[str]:
    return sorted({f"{layer}/{name}.py" for layer, name in _REF.findall(text)})


def test_every_module_named_in_the_architecture_doc_exists():
    named = _named_modules(DOC.read_text(encoding="utf-8"))
    assert named, "ARCHITECTURE.md names no module paths -- the extraction regex has drifted"
    missing = [ref for ref in named if not (ROOT / "ddflow" / ref).is_file()]
    assert not missing, f"ARCHITECTURE.md names module paths that do not exist: {missing}"


def test_the_architecture_doc_describes_the_api_layer():
    """The `api/` package is the application layer both surfaces call; a layer map that
    omits it (as the doc once did) is the drift this file exists to catch."""
    assert "api/" in DOC.read_text(encoding="utf-8"), (
        "ARCHITECTURE.md no longer describes the api/ layer"
    )
