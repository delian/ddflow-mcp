"""The `[plan]` section: how a plan and a claim's declared files are checked.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("plan")
@dataclass
class PlanConfig:
    """Checks on the files a task declares, `[plan]`."""

    warn_missing_globs: bool = knob(
        True,
        doc="Whether `claim` and `plan check` WARN about a task whose declared literal globs (no wildcard) name files that do not exist, with the nearest existing paths (default true). A new file the task is meant to create is fine: the warning only fires when the glob's directory does not exist either, or the file is named like an existing sibling (or exists under that name elsewhere). It is a warning, never a refusal: the claim is made and the plan check keeps its exit code. `false`: no such check.",
    )
