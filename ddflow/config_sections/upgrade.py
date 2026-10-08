"""The `[upgrade]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob

#: What `[upgrade].skew` accepts (decision D-upgrade-skew-guard).
UPGRADE_SKEW_POLICIES = ("refuse", "warn", "off")
#: What `[upgrade].backup` accepts (decision D-upgrade-backups).
UPGRADE_BACKUPS = ("local", "snapshot", "none")
#: What `[upgrade].config_changes` accepts (decision D-upgrade-config-changes).
UPGRADE_CONFIG_CHANGES = ("agent", "ask", "operator")


@declare("upgrade")
@dataclass
class UpgradeConfig:
    """Upgrading ddflow in an onboarded project: the version stamp's skew guard, and what
    `ddflow upgrade --apply` backs up and who may change a config value."""

    skew: str = knob(
        "refuse",
        doc='What happens when a ddflow OLDER than the one that last worked on this project\'s log (the highest `ddflow.seen` stamp) is asked to WRITE. `refuse` (default): the write is refused with exit 3 and the message `Upgrade ddflow-mcp to >= X`; reads always work; an agent that cannot upgrade asks the user and, only if the user insists, reruns with `--allow-older-version --reason "..."` (CLI) or the `allow_older_version` argument (MCP), which records a `skew.overridden` event for THAT session and marks its events as written by an older version. `warn`: write anyway and say so on stderr. `off`: no check. The older version still stamps itself, so the log records that it wrote. Only a ddflow that ships this guard can refuse: releases before it cannot.',
        choices=UPGRADE_SKEW_POLICIES,
        strictest=("refuse", "an older ddflow's write is refused"),
    )

    backup: str = knob(
        "local",
        doc="Where `ddflow upgrade --apply` saves the files it is about to rewrite (decision D-upgrade-backups). `local` (default): a copy under `.ddflow/backups/<stamp>-<from>-to-<to>/` with a `manifest.json` of what was there -- git-ignored, never shared between machines, cost: disk on this machine only; pruned to `backup_keep`. `snapshot`: a git-tracked backup instead -- a tag `ddflow-upgrade-snapshot/<stamp>-<from>-to-<to>` on HEAD (after committing any affected file git did not track yet), shared when pushed, durable, undone with `git checkout <tag> -- <file>` or `ddflow upgrade --restore`; refused (exit 3) with the reason on a tree with uncommitted changes or without git, and files git cannot hold (ignored ones, the git hooks) still get a local copy. `none`: no copy (the files are in git, or you do not want them). `ddflow upgrade --apply --backup MODE` overrides it for one run.",
        choices=UPGRADE_BACKUPS,
        strictest=("local", "a bad value still makes the backup"),
    )
    backup_keep: int = knob(
        10,
        doc="How many local upgrade backups (`.ddflow/backups/`) are kept: after an apply writes a new one, the oldest beyond this many are removed. 0 keeps every one.",
    )
    config_changes: str = knob(
        "agent",
        doc="Who may apply an upgrade's config change (decision D-upgrade-config-changes). `agent` (default): an agent applies a change to a knob the project never set (it takes the new default; the plan, the notice and `upgrade.applied` always list it), while a value anyone set -- in a config layer, with `ddflow config` -- is never changed without the operator: `ddflow upgrade --apply` refuses it (exit 3) unless `--confirm KNOB --reason WHY` is passed. `ask`: every config change waits for `--confirm`, so the agent asks the operator first. `operator`: the same, and the plan marks each as the operator's to apply.",
        choices=UPGRADE_CONFIG_CHANGES,
        strictest=("operator", "a bad value makes the operator decide every config change"),
    )
