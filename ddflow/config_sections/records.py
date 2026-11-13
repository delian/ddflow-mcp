"""The `[records]` section: how `ddflow correct` writes a `record.corrected` event.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("records")
@dataclass
class RecordsConfig:
    """Record correction (D-record-correction-layers-2), `[records]`."""

    correct_digest_chars: int = knob(
        12,
        doc="Characters of the SHA-256 digest `ddflow correct` keeps in a correction's audit entry when a digest is asked for (`correct_digest_old`, `correct_digest_new`). At most the width of the hash.",
        check=int_at_least(1),
    )
    correct_min_digest_chars: int = knob(
        8,
        doc="A value shorter than this many characters gets no digest even when one is asked for: the digest of a short value (a password, a token) lets anyone holding the log confirm a guess.",
        check=int_at_least(1),
    )
    correct_digest_old: bool = knob(
        False,
        doc="Keep a digest of the OLD value in a correction's audit entry. Off by default: the log cannot tell a secret from a typo, and a digest of a low-entropy value lets a guess be confirmed. `ddflow correct --secret` keeps it off whatever this says. The old text itself stays in the log until a scrub, with or without a digest.",
    )
    correct_digest_new: bool = knob(
        False,
        doc="Keep a digest of the NEW value in a correction's audit entry. Off by default, for the reason `correct_digest_old` gives; `ddflow correct --secret` keeps it off whatever this says.",
    )
    correct_reason_max_chars: int = knob(
        500,
        doc="Longest reason `ddflow correct` accepts. The reason says why the value was wrong, never what it was: a long one is usually the old text pasted in.",
        check=int_at_least(1),
    )
    correct_max_fields: int = knob(
        8,
        doc="Most fields one `ddflow correct` may replace in one event.",
        check=int_at_least(1),
    )
