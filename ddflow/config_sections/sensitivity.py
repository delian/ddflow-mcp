"""The `[sensitivity]` section: write-time labelling (SE-sensitivity).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("sensitivity")
@dataclass
class SensitivityConfig:
    """Labels on records, `[sensitivity]` (decision D-shared-engines-r2 7)."""

    scan_at_write: bool = knob(
        True,
        doc="Scan a record's text for secrets and personal data when it is added or edited, and raise its `sensitivity` label to the level the finding needs (secret: confidential; email, name or host name: restricted). A label is only ever raised by the scan; lowering one is an explicit edit. Off: a record carries only the label it was given.",
    )
