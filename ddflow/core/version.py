"""Which ddflow is running (B-uni-compat-config).

The reader of the running version: `services/install_info`, the export registry and the
export writer use it. (`infra/log.running_version` is the one copy left, to delegate here
when that file is free of another lane's claim.) The source-tree test and the upgrade advice
live in `config_sections/_compat` (the bottom layer, which `config` itself may import), and
`services/install_info` fits the advice to how ddflow is installed.

Pure: no disk, no git, no subprocess.
"""

from __future__ import annotations

import ddflow


def running() -> str:
    """The version of the code that is running, read at call time (a test sets it);
    "" when the package declares none."""
    return str(getattr(ddflow, "__version__", "") or "")


def format_level() -> int:
    """The on-disk FORMAT_LEVEL of the code that is running, read at call time (a test sets
    it); 0 when the package declares none, which compares as "no format" and never refuses."""
    raw = getattr(ddflow, "FORMAT_LEVEL", 0)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
