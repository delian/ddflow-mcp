"""Adapter selection from `[adapters]`: which adapter serves a multi-adapter port, and the
consent a caller needs before it reaches outside this clone or writes a shared file.

The bottom `ddflow.adapters` layer imports no ddflow config (r-core-pure), so the code that
READS `cfg.adapters.*` -- and turns it into an adapter or a consent decision -- lives here,
one layer up, exactly as `config_store` supplies the default `ConfigStore` that reads
`ddflow.config`. A caller that must pick a `Platform`, a `Transport`, or honour network /
managed-file consent asks this module; it never imports `ddflow.adapters.platform` or
`ddflow.adapters.transport` at the call site.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ddflow.adapters.platform import Platform, Posix, Windows
from ddflow.adapters.transport import TRANSPORTS, Transport

if TYPE_CHECKING:  # the runtime import is a no-op here: selection takes the cfg it reads
    from ddflow.config import Config

__all__ = [
    "managed_files_backup",
    "managed_files_consented",
    "network_consented",
    "selected_lane_transport",
    "selected_platform",
]


def selected_platform(cfg: Config) -> Platform:
    """The `Platform` adapter `[adapters].platform` selects: the Posix adapter for 'posix',
    and for 'auto' (the default) this host's OS -- Posix here, `Windows` (unavailable) on an
    unsupported OS, per D-adapters-answers-3. A value outside the two is already refused by
    the enum check (config falls back to its strictest, 'posix')."""
    if cfg.adapters.platform == "posix" or os.name == "posix":
        return Posix()
    return Windows()


def selected_lane_transport(cfg: Config) -> type[Transport]:
    """The `Transport` adapter `[adapters].lane_transport` selects, resolved from the
    `TRANSPORTS` registry; `get_or_raise` lists those known when it is none of them."""
    return TRANSPORTS.get_or_raise(cfg.adapters.lane_transport)


def network_consented(cfg: Config, kind: str) -> bool:
    """Whether `[adapters].network_consent` grants `kind` (one of
    `NETWORK_KINDS`). Absent is DECLINED, never granted (r-consent-outward,
    D-adapters-answers-3)."""
    return bool(cfg.adapters.network_consent.get(kind, False))


def managed_files_consented(cfg: Config, kind: str) -> bool:
    """Whether `[adapters].managed_files_consent` grants a `ManagedFile` merge of `kind`.
    Absent is DECLINED, never silently written (D-adapters-answers-2)."""
    return bool(cfg.adapters.managed_files_consent.get(kind, False))


def managed_files_backup(cfg: Config) -> bool:
    """Whether the `ManagedFile` adapter writes the one-time `.ddflow-bak` beside a shared
    file before its first write (`[adapters].managed_files_backup`, D-adapters-answers-2)."""
    return bool(cfg.adapters.managed_files_backup)
