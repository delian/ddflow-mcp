"""The environment a gate command or the CI command is given.

A shell that exports `DDFLOW_AGENT` (every fleet agent's does) handed it to the project's
test command, and three `test_clone_id_recovery` tests that read the caller's identity failed
in the gate yet passed with the variable unset (B3a8e04ca38). The identity variables ddflow
reads are the caller's, not the suite's, so they are removed from the child's environment:
`[gates].scrub_env` names them (plus any the project adds), `[gates].keep_env` and
`[ci].keep_env` list the ones to preserve.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from ..config import Config
from ..config_sections.gates import GatesConfig


def child_env(
    cfg: Config | None = None,
    base: Mapping[str, str] | None = None,
    *,
    ci: bool = False,
) -> dict[str, str]:
    """``base`` (default `os.environ`) without the scrubbed variables, minus the kept ones."""
    gates = cfg.gates if cfg is not None else GatesConfig()
    keep: set[str] = set(gates.keep_env)
    if ci and cfg is not None:
        keep |= set(cfg.ci.keep_env)
    scrub = set(gates.scrub_env) - keep
    return {k: v for k, v in (os.environ if base is None else base).items() if k not in scrub}
