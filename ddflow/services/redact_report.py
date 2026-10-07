"""Redaction for anything that leaves the machine, from the loaded config.

The engine and its named profiles are `core.redact` (B-uni-textkit.4); `core` cannot read the
config, so this module supplies what the config holds -- the session secret patterns and the
project names -- and re-exports what callers imported from here.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterable
from pathlib import Path

from ..config import Config, SessionConfig
from ..core.redact import (
    PROFILES,
    PUBLIC_NAMES,
    Redacted,
    Redactor,
    mask_secrets,
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
    "redact_secrets",
    "redactor",
    "session_patterns",
]


def names_for(cfg: Config | None) -> list[str]:
    """Project names to redact as words: ``[upstream].redact_extra`` if that section exists."""
    up = getattr(cfg, "upstream", None)
    return [str(n) for n in (getattr(up, "redact_extra", None) or [])]


def session_patterns(cfg: Config | None) -> list[str]:
    """The session redaction patterns of ``cfg``, or the built-in defaults."""
    session = cfg.session if cfg is not None else SessionConfig()
    return [*session.redact_patterns, *session.redact_extra]


def _machine_name() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""


def _cwd_repo_root() -> str:
    try:
        cwd = Path.cwd()
    except OSError:
        return ""
    for d in (cwd, *cwd.parents):
        if (d / ".git").exists():
            return str(d)
    return str(cwd)


def _resolved(
    hostname: str | None,
    home: str | os.PathLike[str] | None,
    repo_root: str | os.PathLike[str] | None,
) -> dict[str, str]:
    """`None` is the machine's own: its name, `$HOME`, the repo found from the working directory."""
    return {
        "hostname": hostname if hostname is not None else _machine_name(),
        "home": str(home) if home is not None else os.path.expanduser("~"),
        "repo_root": str(repo_root) if repo_root is not None else _cwd_repo_root(),
    }


def redact_secrets(text: str, cfg: Config | None) -> tuple[str, int]:
    """Only the secrets, context-preserving: (clean text, count)."""
    return mask_secrets(text, session_patterns(cfg))


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
    if profile not in PROFILES:
        raise ValueError(f"unknown redaction profile {profile!r}; one of {', '.join(PROFILES)}")
    base = PROFILES[profile]
    return Redactor(
        profile,
        secret_patterns=session_patterns(cfg),
        names=names_for(cfg) if names is None else names,
        **_resolved(
            base.hostname if hostname is None else hostname,
            base.home if home is None else home,
            base.repo_root if repo_root is None else repo_root,
        ),
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
    """Redact `text` for an upstream report: `None` for hostname, home and repo_root is the
    machine's own (`core.redact.redact_text` with the config's patterns)."""
    patterns = list(secret_patterns) if secret_patterns is not None else session_patterns(cfg)
    return redact_text(
        text,
        secret_patterns=patterns,
        names=names,
        **_resolved(hostname, home, repo_root),
    )
