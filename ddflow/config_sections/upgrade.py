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
#: What `[upgrade].auto` accepts (decision D-upgrade-auto-check).
UPGRADE_AUTO = ("off", "check", "safe")
#: What `[upgrade].on_start` accepts (decision D-self-upgrade 5).
UPGRADE_ON_START = ("off", "check", "safe")


@declare("upgrade")
@dataclass
class UpgradeConfig:
    """Upgrading ddflow in an onboarded project: the version stamp's skew guard, and what
    `ddflow upgrade --apply` backs up and who may change a config value."""

    skew: str = knob(
        "refuse",
        doc='What happens when a ddflow OLDER than the one that last worked on this project\'s log (the highest `ddflow.seen` stamp) is asked to WRITE. `refuse` (default): the write is refused with exit 3 and the message `Upgrade ddflow-mcp to >= X` (an installed ddflow; a source checkout is told to merge main, naming the version); reads always work; an agent that cannot upgrade asks the user and, only if the user insists, reruns with `--allow-older-version --reason "..."` (CLI) or the `allow_older_version` argument (MCP), which records a `skew.overridden` event for THAT session and marks its events as written by an older version. `warn`: write anyway and say so on stderr. `off`: no check. The older version still stamps itself, so the log records that it wrote. Only a ddflow that ships this guard can refuse: releases before it cannot.',
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

    auto: str = knob(
        "check",
        doc="What ddflow does when it finds itself upgraded (decision D-upgrade-auto-check): the running version is newer than the one the project was last brought up to and `ddflow upgrade --plan` has items. `check` (default): the brief (so the SessionStart hook too) and the MCP instructions say so in ONE line, `Upgraded ddflow 0.1.9 -> 0.1.10: run ddflow upgrade --plan`, once per version on this machine for this project (a git-ignored marker, `.ddflow/local/upgrade-notice.json`, is the only thing written). `safe`: the same line, after applying the plan's non-destructive categories (`hooks` and `instructions`) with a backup first (`[upgrade].backup`); config defaults, migrations, repairs and features stay the operator's. `off`: no notice, no write. A running MCP server whose code is older than the installed package or the log's highest stamp also says `restart the server`, once (not governed by this knob).",
        choices=UPGRADE_AUTO,
        strictest=("check", "a bad value still notices but applies nothing"),
    )

    on_start: str = knob(
        "safe",
        doc="What a starting `ddflow mcp` server or container does when the running ddflow is NEWER than the one this project was last brought up to (decisions D-self-upgrade 5 and D-upgrade-on-mcp-connect). It never refuses to start and never waits longer than `start_timeout_s`: it serves and leaves what it did not finish as pending. `safe` (default): rebuild the derived stores (the sqlite index), and in a CONTAINER (the operator chose to run the newer image) apply the plan's non-destructive categories (`hooks` and `instructions`) with a backup first; on an MCP connect outside a container nothing else is applied without the operator, so the handshake PROPOSES the plan and says how to apply it once the operator agrees. `check`: no write at all, the proposal only. `off`: nothing. What was done and what waits is in the handshake instructions and the first brief; a read-only or foreign-owned project is reported, never written. The environment variable `DDFLOW_UPGRADE_ON_START` (`off`, `check` or `safe`) overrides this knob for one run, e.g. `docker run -e DDFLOW_UPGRADE_ON_START=check`; an unknown value counts as `check`.",
        choices=UPGRADE_ON_START,
        strictest=("check", "a bad value proposes the upgrade and writes nothing"),
    )
    start_timeout_s: float = knob(
        10.0,
        doc="Seconds a starting MCP server or container spends on the `on_start` work before it serves anyway and reports the rest as pending (decision D-self-upgrade 5: bounded time, never a failed start). 0 or less skips the work entirely.",
    )
