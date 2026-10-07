"""The `[release]` section: what a release checks before it ships.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob

#: What `[release].manifest_lint` accepts (decision D-upgrade-manifest-lint).
RELEASE_LINT_POLICIES = ("block", "warn", "off")


@declare("release")
@dataclass
class ReleaseConfig:
    """Release checks: the upgrade-manifest lint."""

    manifest_lint: str = knob(
        "block",
        doc="What a release does when the code changes a config knob (added, default changed, removed) or an event kind that the shipped upgrade manifest (`ddflow/templates/upgrade/`) does not announce. `block` (default): the release stops -- `ddflow version lint`, `ddflow version cut`, scripts/release.sh and the publish workflow exit 3 -- and the message lists the unmanifested changes and the operator's options: have an agent write the manifest entries pre-filled from the diff, waive named changes with `ddflow version lint --waive <change> --reason ...` (recorded, and shown in the next upgrade plan), or change this policy. `warn`: print the same and carry on. `off`: no check. Choosing to waive or to lower the policy is the operator's decision, not an agent's.",
        choices=RELEASE_LINT_POLICIES,
        strictest=("block", "an unannounced change stops the release"),
    )
