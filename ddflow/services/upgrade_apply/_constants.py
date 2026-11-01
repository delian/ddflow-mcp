"""The `ddflow upgrade --apply` vocabulary: item status words and mode policies.

This is the leaf submodule of the ``upgrade_apply`` package: the appliers and the apply
loop import these from here, so neither imports the other for a shared word.
"""

from __future__ import annotations

from ...config_sections.upgrade import UPGRADE_BACKUPS

BACKUP_MODES = UPGRADE_BACKUPS
#: What `[upgrade].config_changes` accepts: who may apply a config change on a knob nobody set.
CONFIG_POLICIES = ("agent", "ask", "operator")

#: The categories whose items the manifest drives: they alone decide whether the project's
#: version moves up to the running one.
_VERSIONED = ("config", "features")

APPLIED = "applied"
ACKNOWLEDGED = "acknowledged"
REFUSED = "refused"
FAILED = "failed"
UNAVAILABLE = "unavailable"
SKIPPED = "skipped"
