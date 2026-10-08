"""Which ddflow is running (B-uni-compat-config).

The one reader of the running version. Before this it was read four ways (`infra/log`,
`services/install_info`, the export registry and the export writer). The source-tree test
and the upgrade advice live in `config_sections/_compat` (the bottom layer, which `config`
itself may import), and `services/install_info` fits the advice to how ddflow is installed.

Pure: no disk, no git, no subprocess.
"""

from __future__ import annotations

import ddflow


def running() -> str:
    """The version of the code that is running, read at call time (a test sets it);
    "" when the package declares none."""
    return str(getattr(ddflow, "__version__", "") or "")
