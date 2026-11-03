"""The hook's read of the lease snapshot (LK-hook-snapshot): the implementation lives in
`ddflow.core.leasesnap` (stdlib only, so the writer in `services.leases.snapshot` can import it
without a cycle and the hook without the enforce package); this module is its enforce-side name."""

from __future__ import annotations

from ...core.leasesnap import (  # noqa: F401
    CONFIG_FILES,
    MAX_CONFIG_BYTES,
    MAX_SKEW_S,
    MISSING,
    NAME,
    NEWER,
    OK,
    REFRESH,
    SCHEMA,
    STALE,
    UNREADABLE,
    UNREADABLE_CONFIG,
    Snapshot,
    _lease,
    config_stamp,
    path_of,
    read,
)
