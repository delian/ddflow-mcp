"""The `[upstream_gh]` section: the `gh` CLI adapter's caps (`ddflow/infra/upstream_gh`).

A new file here is a new section, nothing more registers (`ddflow.config` assembles
`Config` by convention). The adapter truncates a `gh` error message to `error_max`
characters before a caller raises, so a failure report never outgrows a log line before
the caller redacts it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("upstream_gh")
@dataclass
class UpstreamGhConfig:
    """Caps of the `gh` CLI adapter; the call decides consent, never this module."""

    error_max: int = knob(
        400,
        doc="Characters of a `gh` error message kept when an `Unavailable`/`GhError` is raised (`str(exc)[:error_max]`), so a failure report never outgrows a log line before the caller redacts it.",
        check=int_at_least(1),
    )
