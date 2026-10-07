"""The `[upgrade]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob

#: What `[upgrade].skew` accepts (decision D-upgrade-skew-guard).
UPGRADE_SKEW_POLICIES = ("refuse", "warn", "off")


@declare("upgrade")
@dataclass
class UpgradeConfig:
    """Upgrading ddflow in an onboarded project: the version stamp's skew guard."""

    skew: str = knob(
        "refuse",
        doc='What happens when a ddflow OLDER than the one that last worked on this project\'s log (the highest `ddflow.seen` stamp) is asked to WRITE. `refuse` (default): the write is refused with exit 3 and the message `Upgrade ddflow-mcp to >= X`; reads always work; an agent that cannot upgrade asks the user and, only if the user insists, reruns with `--allow-older-version --reason "..."` (CLI) or the `allow_older_version` argument (MCP), which records a `skew.overridden` event for THAT session and marks its events as written by an older version. `warn`: write anyway and say so on stderr. `off`: no check. The older version still stamps itself, so the log records that it wrote. Only a ddflow that ships this guard can refuse: releases before it cannot.',
        choices=UPGRADE_SKEW_POLICIES,
        strictest=("refuse", "an older ddflow's write is refused"),
    )
