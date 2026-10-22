"""The `[grants]` section: limits on the operator's recorded grants (`ddflow grant add`,
DX-operator-grants-recorded).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("grants")
@dataclass
class GrantsConfig:
    """Operator grants: a person's recorded permission for what a budget refuses, such as an
    extra review round past [review].max_rounds."""

    max_count: int = knob(
        3,
        doc="The most extra rounds ONE operator grant may carry (default 3): `ddflow grant add --count N` above it is refused. A grant is the operator's recorded permission for rounds past [review].max_rounds (`ddflow grant list`); a standing allowance of many rounds is a raised max_rounds, which is a decision, not a grant.",
    )
    ttl_h: int = knob(
        48,
        doc="How many hours an operator grant stays usable after it is added (default 48; 0 = never expires). An expired grant is still listed, marked expired, and a review no longer cites it.",
    )
