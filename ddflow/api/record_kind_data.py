"""Record kinds declared as data: TOML descriptor -> `RecordKind` -> the registry.

``ddflow/templates/kinds/<name>.toml`` is a kind in the form `core.kinddata` parses; this
turns it into the `RecordKind` `api.records` declares, so a kind with no behaviour needs no
Python. A kind whose ``def_kind`` is not yet a definition kind gets one of its own.

Python is for kinds with behaviour (``ops``, ``weakening``); a descriptor naming either is
refused. This module does not import `api.records` (which loads its own data kinds while it
is being imported): the module is passed in, or found when it is already loaded.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any

from ..core import defs as D
from ..core import kinddata as KD

KINDS_DIR = Path(__file__).resolve().parent.parent / "templates" / "kinds"


def _records(records: ModuleType | None) -> ModuleType:
    return records or import_module("ddflow.api.records")


def kind_from_data(desc: dict[str, Any], records: ModuleType | None = None) -> Any:
    """Build the `RecordKind` for a parsed descriptor (`core.kinddata.parse`)."""
    R = _records(records)
    if code := [k for k in KD.CODE_KEYS if k in desc]:
        raise KD.KindDataError(f"{code[0]} is code; a kind with behaviour is written in Python")
    args = dict(desc)
    args["fields"] = tuple(R.FieldSpec(**f) for f in args["fields"])
    rest = args.pop("declined_other", "")
    verbs = tuple(args.get("verbs", R.VERBS))
    if rest:
        args["declined"] = {
            **{v: rest for v in R.VERBS if v not in verbs},
            **args.get("declined", {}),
        }
    for key, cls in (("export", R.ExportSpec), ("feed", R.FeedSpec)):
        if key in args:
            args[key] = cls(**args[key])
    # a definition kind is added only for a descriptor that stands (a failed one leaves none)
    held = args["def_kind"] in D.DEF_KINDS
    D.register_kind(args["def_kind"], args["summary"])
    try:
        return R.RecordKind(**args)
    except Exception:
        if not held:
            D.DEF_KINDS.pop(args["def_kind"], None)
        raise


def parse_kind(text: str, *, source: str = "", records: ModuleType | None = None) -> Any:
    """The kind a descriptor's TOML text declares (not yet registered)."""
    return kind_from_data(KD.parse(text, source=source), records)


def packaged() -> Iterator[tuple[str, str]]:
    """``(kind name, text)`` of every packaged descriptor, by name: ``load_packaged`` takes the name."""
    for entry in sorted(KINDS_DIR.glob("*.toml")):
        yield entry.stem, entry.read_text(encoding="utf-8")


def load_packaged(name: str, records: ModuleType | None = None) -> Any:
    """Declare the packaged kind ``name`` and return it."""
    R = _records(records)
    text = (KINDS_DIR / f"{name}.toml").read_text(encoding="utf-8")
    return R.declare(parse_kind(text, source=f"{name}.toml", records=R))
