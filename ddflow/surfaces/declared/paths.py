"""The `paths` command: the workflow-path variants (WP-path-variants).

One declaration for the CLI and the MCP entry. The `metrics` view `paths` renders the burst
view; :mod:`..services.metrics.path_variants` is the variant lens (directly-follows, top
variants, overlap, superseded passes, out-of-order) this command renders. Read-only."""

from __future__ import annotations

from ..registry import Command, by_tool
from ..tools._common import _api

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("paths",),
        summary="workflow-path variants: directly-follows, top variants, burst overlap, superseded passes, out-of-order",
        tool="ddflow_paths",
        family="ddflow_view",
        action="paths",
        tier="all",
        help_group="view",
        description="The workflow-path variant lens over the event log: the directly-follows gate graph with median gaps, the top ordered gate sequences, the burst overlap (union vs sum), passes superseded by a later tree change, and gates run out of the canonical order.",
        call=lambda repo, a, agent: _api().run_metrics(repo, view="variants", agent=agent),
        payload=(
            "now",
            "metrics",
            "text",
        ),
        render=lambda out, a: out.data["text"],
        params=(),
    ),
)

BY_TOOL = by_tool(COMMANDS)
