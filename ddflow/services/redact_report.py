"""Redaction for anything that leaves the machine, from the loaded config.

The engine and its named profiles are `core.redact` (B-uni-textkit.4); `core` cannot read the
config, so this module supplies what the config holds -- the session secret patterns and the
project names -- and re-exports what callers imported from here.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from ..config import Config, SessionConfig
from ..core.redact import (
    PUBLIC_NAMES,
    Redacted,
    Redactor,
    private_addresses,
    redact_text,
)

__all__ = [
    "PUBLIC_NAMES",
    "Redacted",
    "Redactor",
    "names_for",
    "private_addresses",
    "redact_report",
    "redactor",
    "secret_patterns",
]


def names_for(cfg: Config | None) -> list[str]:
    """Project names to redact as words: ``[upstream].redact_extra`` if that section exists."""
    up = getattr(cfg, "upstream", None)
    return [str(n) for n in (getattr(up, "redact_extra", None) or [])]


def secret_patterns(cfg: Config | None) -> list[str]:
    """The session redaction patterns of ``cfg``, or the built-in defaults."""
    session = cfg.session if cfg is not None else SessionConfig()
    return [*session.redact_patterns, *session.redact_extra]


def redactor(
    profile: str,
    cfg: Config | None = None,
    *,
    names: Iterable[str] | None = None,
    hostname: str | None = None,
    home: str | None = None,
    repo_root: str | None = None,
) -> Redactor:
    """The `Redactor` of a named profile, with ``cfg``'s patterns and (unless given) names."""
    return Redactor(
        profile,
        secret_patterns=secret_patterns(cfg),
        names=names_for(cfg) if names is None else names,
        hostname=hostname,
        home=home,
        repo_root=repo_root,
    )


def redact_report(
    text: object,
    *,
    hostname: str | None = None,
    names: Iterable[str] = (),
    home: str | os.PathLike[str] | None = None,
    repo_root: str | os.PathLike[str] | None = None,
    cfg: Config | None = None,
    secret_patterns: Iterable[str] | None = None,
) -> Redacted:
    """Redact `text` for an upstream report (`core.redact.redact_text` with the config's patterns)."""
    patterns = (
        list(secret_patterns) if secret_patterns is not None else globals()["secret_patterns"](cfg)
    )
    return redact_text(
        text,
        secret_patterns=patterns,
        hostname=hostname,
        names=names,
        home=home,
        repo_root=repo_root,
    )
