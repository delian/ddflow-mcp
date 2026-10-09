"""Which gates an item runs: the pipeline of its kind (the registry in
`config_sections._kinds` and `[gates].kind_pipelines`) and which of those gates apply to
the work it declares (`[gate.<id>] applies_when`)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from ...config import Config
from ...config_sections._kinds import KINDS
from ...core.globs import overlap
from ...core.model import Item

if TYPE_CHECKING:
    from .defs import GateDef


def kind_pipeline(cfg: Config, kind: str) -> list[str]:
    """The gate ids an item of ``kind`` passes through: the ``[gates].kind_pipelines``
    entry when it names the kind, else the kind's built-in pipeline; an unregistered kind
    runs the task pipeline, as every kind did before the registry."""
    override = cfg.gates.kind_pipelines.get(kind)
    if override is not None:
        return list(override)
    spec = KINDS.get(kind)
    return list(getattr(cfg.gates, spec.base if spec else "task_pipeline"))


def kind_pipelines(cfg: Config) -> dict[str, list[str]]:
    """Every registered kind's effective pipeline, in registry order."""
    return {name: kind_pipeline(cfg, name) for name in KINDS}


def gate_applies(gdef: GateDef | None, item: Item) -> str:
    """Why ``gdef`` does NOT apply to ``item``, or ``""`` when it does.

    The predicate behind `[gate.<id>] applies_when`: a gate with path patterns applies
    only when one of them can cover one of the item's declared globs. An undefined gate, a
    gate without patterns and an item that declares no globs always apply, so a gate is
    never left out for want of information. The reason names the patterns and the globs it
    was judged against, because a gate that does not apply is reported, not silently
    absent."""
    if gdef is None or not gdef.applies_when or not item.globs:
        return ""
    if any(overlap(p, g) for p in gdef.applies_when for g in item.globs):
        return ""
    return (
        f"applies only to work touching {', '.join(gdef.applies_when)}; "
        f"{item.id} declares {', '.join(item.globs)}"
    )


def not_applicable(
    item: Item, pipeline: Iterable[str], gates: Mapping[str, GateDef] | None
) -> dict[str, str]:
    """Gate -> why it does not apply, for the ``pipeline`` gates ``item`` does not run.

    A gate with a recorded outcome always applies: work already done is never hidden."""
    if not gates:
        return {}
    out: dict[str, str] = {}
    for gid in pipeline:
        if item.gate_outcome(gid):
            continue
        why = gate_applies(gates.get(gid), item)
        if why:
            out[gid] = why
    return out
