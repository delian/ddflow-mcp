"""Which gate pipeline an item of a kind runs: the lookup over the kind registry
(`config_sections._kinds`) and `[gates].kind_pipelines`."""

from __future__ import annotations

from ...config import Config
from ...config_sections._kinds import KINDS


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
