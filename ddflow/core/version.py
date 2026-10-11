"""Which ddflow is running (B-uni-compat-config).

The reader of the running version: `services/install_info`, the export registry, the
export writer and `infra/log.running_version` use it. The source-tree test and the upgrade advice
live in `config_sections/_compat` (the bottom layer, which `config` itself may import), and
`services/install_info` fits the advice to how ddflow is installed.

Pure: no disk, no git, no subprocess.
"""

from __future__ import annotations

import ddflow

from .events import is_older, version_key


def running() -> str:
    """The version of the code that is running, read at call time (a test sets it);
    "" when the package declares none."""
    return str(getattr(ddflow, "__version__", "") or "")


def format_level() -> int:
    """The on-disk FORMAT_LEVEL of the code that is running, read at call time (a test sets
    it); 0 when the package declares none, which compares as "no format" and never refuses."""
    raw = getattr(ddflow, "FORMAT_LEVEL", 0)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0


def _release_parts(version: str) -> tuple[int, int, int] | None:
    """(major, minor, patch) of a final release version, else None: a version that does not
    parse, or a pre-release (`0.5.7rc1`), has no patch line to compare."""
    key = version_key(version)
    if not key or key[1] != (1,):
        return None
    nums = (*key[0], 0, 0, 0)
    return nums[0], nums[1], nums[2]


def patch_only_skew(running_version: str, highest: str) -> bool:
    """Is ``running_version`` older than ``highest`` by PATCH releases only: the same major
    and minor, a lower patch, both final releases (decision D-mu-bugfix-answers)? The caller
    adds the other two conditions -- the same FORMAT_LEVEL and no capability gap -- because
    those come from the log, not from the two version strings. Pure."""
    mine, theirs = _release_parts(running_version), _release_parts(highest)
    return (
        mine is not None
        and theirs is not None
        and mine[:2] == theirs[:2]
        and is_older(running_version, highest)
    )
