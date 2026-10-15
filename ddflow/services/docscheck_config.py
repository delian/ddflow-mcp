"""The documentation check as the project's `[docs]` section configures it.

``docscheck.check_docs`` takes plain values; this reads them from the config, so the check
command (and anything else that checks docs the way the project asked) calls one function.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from .docscheck import check_docs
from .docscheck_help import command_runner
from .docscheck_types import Report


def check_configured(repo, cfg: Config, *, run_commands: bool = True, **options: Any) -> Report:
    """``check_docs`` with ``[docs].extract``, ``.count``, ``.cli_command`` and
    ``.command_timeout_s`` applied. ``run_commands=False`` spawns nothing: help is not walked
    and counts are noted as not recomputed. ``options`` pass through to ``check_docs``."""
    docs = cfg.docs
    return check_docs(
        repo,
        extract=docs.extract,
        count=docs.count,
        runner=command_runner(repo, docs.command_timeout_s) if run_commands else None,
        help_commands=docs.cli_command or None,
        **options,
    )
