"""Which document types a project has, and which documents they name (B-dt-rules).

The shipped types, then `.ddflow/doctypes/*.toml`; a later layer replaces or extends an earlier
one (`spec.resolve`). Shared by the docs check (validate) and the guidance a document's type
hands an agent, so the two never disagree about what a type's documents are.
"""

from __future__ import annotations

from pathlib import Path
from string import Formatter

from . import spec

PROJECT_DIR = ".ddflow/doctypes"
#: A shipped type's `DocType.source` starts with this (the layer label); a project's does not.
SHIPPED = "shipped/"


def type_glob(dt: spec.DocType) -> str:
    """The glob of the documents of ``dt``: a collection's placeholders become ``*``."""
    if not dt.multi:
        return dt.path
    return "".join(
        lit + ("*" if field is not None else "")
        for lit, field, _, _ in Formatter().parse(dt.pattern)
    )


def types_of(repo: Path | str, *, project: bool = True) -> list[spec.DocType]:
    """Every type of the project at ``repo`` (only the shipped ones when ``project`` is false).
    Raises `ValueError` (`spec.DocTypeError`, or a file that is not UTF-8) / `OSError`."""
    layers = [spec.read_layer("shipped", spec.SHIPPED_DIR)]
    if project:
        layers.append(spec.read_layer("project", Path(repo) / PROJECT_DIR))
    return spec.registry(layers).values_list()
