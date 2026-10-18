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

    model_allowed: list[str] = knob(
        factory=list,
        doc="Sensitivity levels that may be sent to a model although the vocabulary marks them not to be (restricted and confidential are not, by default). A level named here is shown at the doors in `egress_doors`; empty (default) keeps the vocabulary's rule.",
        check=lambda v: (
            ""
            if isinstance(v, list) and all(isinstance(x, str) for x in v)
            else "must be a list of level names"
        ),
    )
    egress_doors: list[str] = knob(
        factory=lambda: ["search", "recall"],
        doc="The doors that feed a model, where a record whose sensitivity may not go to a model is withheld: search, recall (brief, export, hub publish and notifications follow when they carry labelled records). Empty turns the filter off.",
        check=lambda v: (
            ""
            if isinstance(v, list) and all(isinstance(x, str) for x in v)
            else "must be a list of door names"
        ),
    )
