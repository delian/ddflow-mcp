"""Record kinds declared as data: TOML descriptor -> `RecordKind` -> the registry.

``ddflow/templates/kinds/<name>.toml`` is a kind in the form `core.kinddata` parses; this
turns it into the `RecordKind` `api.records` declares, so a kind with no behaviour needs no
Python. A kind whose ``def_kind`` is not yet a definition kind gets one of its own.

Python is for kinds with behaviour (``ops``, ``weakening``); a descriptor naming either is
refused.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core import defs as D
from ..core import kinddata as KD

if TYPE_CHECKING:
    from . import records as R

KINDS_DIR = Path(__file__).resolve().parent.parent / "templates" / "kinds"


def kind_from_data(desc: dict[str, Any]) -> R.RecordKind:
    """Build the `RecordKind` for a parsed descriptor (`core.kinddata.parse`)."""
    from . import records as R  # late: records loads its own data kinds at import

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
    D.register_kind(args["def_kind"], args["summary"])
    return R.RecordKind(**args)


def parse_kind(text: str, *, source: str = "") -> R.RecordKind:
    """The kind a descriptor's TOML text declares (not yet registered)."""
    return kind_from_data(KD.parse(text, source=source))


def packaged() -> Iterator[tuple[str, str]]:
    """``(file name, text)`` of every packaged descriptor, by name."""
    for entry in sorted(KINDS_DIR.glob("*.toml")):
        yield entry.name, entry.read_text(encoding="utf-8")


def load_packaged(name: str) -> R.RecordKind:
    """Declare the packaged kind ``name`` and return it."""
    text = (KINDS_DIR / f"{name}.toml").read_text(encoding="utf-8")
    from . import records as R

    return R.declare(parse_kind(text, source=f"{name}.toml"))
