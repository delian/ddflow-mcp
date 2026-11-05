"""Control-plane facts (CP-verify-facts): the probes' results, in the one facts table.

Data is `templates/control_plane_facts.toml`, a table per plane or ACP adapter of the shared
record (`facts`). A control plane has no harness descriptor, so these rows join `ddflow harness
facts` from here. A project overrides or adds a fact in `.ddflow/control_plane_facts.toml`:
re-probing a plane must not wait for a ddflow release. A plane id may not reuse a harness id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..adapters.toml import TomlError, TomlIo, TomlLibIo
from ..infra import paths
from . import facts, harnessreg
from .harnessfacts import show

FILE = "control_plane_facts.toml"


class FactsError(ValueError):
    """The project's facts file cannot be used; the message names it."""


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """`extra` over `base`, one fact at a time, so an override leaves its neighbours."""
    out = {**base, "sources": {**base.get("sources", {}), **extra.get("sources", {})}}
    planes = {k: dict(v) for k, v in base.get("plane", {}).items()}
    for name, row in extra.get("plane", {}).items():
        if not isinstance(row, dict):
            raise FactsError(f"{FILE}: [plane.{name}] is not a table")
        planes.setdefault(name, {}).update(row)
    out["plane"] = planes
    return out


def load(root: Path | None = None, toml: TomlIo | None = None) -> dict[str, Any]:
    """The shipped facts, with the project's over them when `root` has any. A project file
    that cannot be read, or holds a bad fact, raises FactsError: it is never skipped."""
    io = toml or TomlLibIo()
    data = io.read(paths.templates_dir() / FILE)
    local = Path(root) / ".ddflow" / FILE if root else None
    if local is None or not local.is_file():
        return data
    try:
        merged = _merge(data, io.read(local))
    except (TomlError, OSError) as e:
        raise FactsError(f"{local}: {e}") from e
    bad = problems(merged)
    if bad:
        raise FactsError(f"{local}: " + "; ".join(bad))
    return merged


def _fact_problems(at: str, node: Any, sources: Any) -> list[str]:
    if not facts.is_fact(node):
        return [f"{at} is not a fact"]
    out = facts.problems(at, node, sources)
    status = node.get("s")
    if status in facts.READABLE and "v" not in node:
        out.append(f"{at}: status {status} needs a value")
    if status == "none" and "v" in node:
        out.append(f"{at}: status none takes no value")
    if status == "verified" and not (node.get("probe") and node.get("ver")):
        out.append(f"{at}: verified needs the probe that saw it and the tool version")
    return out


def problems(data: dict[str, Any]) -> list[str]:
    """Every defect of a parsed facts file, one string each."""
    sources = data.get("sources", {})
    harnesses = set(harnessreg.ids())
    out = []
    for name, row in sorted(data.get("plane", {}).items()):
        if name in harnesses:
            out.append(f"plane {name!r} reuses a harness id")
        for key, node in sorted(row.items()):
            out += _fact_problems(f"plane.{name}.{key}", node, sources)
    return out


def usable(plane: dict[str, Any], key: str) -> Any:
    """A fact's value only when it is believable; a lead or an absence reads as None."""
    node = plane.get(key)
    return node.get("v") if facts.is_fact(node) and node["s"] in facts.READABLE else None


def ids(data: dict[str, Any]) -> list[str]:
    return sorted(data.get("plane", {}))


def rows(data: dict[str, Any], plane: str = "") -> list[dict[str, str]]:
    """Table rows in `harnessfacts` shape, for one plane or all of them."""
    out = []
    for name in [plane] if plane else ids(data):
        for key, node in sorted(data["plane"][name].items()):
            out.append(
                {
                    "agent": name,
                    "fact": key,
                    "value": show(node["v"]) if "v" in node else "",
                    "status": node["s"],
                    "source": node["src"],
                    "date": node["d"],
                    "tool_version": node.get("ver", ""),
                    "note": node.get("note", ""),
                }
            )
    return out
