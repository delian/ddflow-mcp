"""Redaction for anything that leaves the machine in an upstream report.

The engine moved to `core.redact` (B-uni-textkit.4), which holds the one Redactor and its
named profiles; this module re-exports what callers imported from here.
"""

from __future__ import annotations

from ..core.redact import (
    PUBLIC_NAMES,
    Redacted,
    private_addresses,
    redact_report,
)

__all__ = ["PUBLIC_NAMES", "Redacted", "private_addresses", "redact_report"]
