"""The effective Claude Code subagent prompt-cache TTL, and the doctor warning when it is 5m.

TE-subagent-ttl / D-TE-1: 52% of subagent spend is whole-context cache re-writes after waits
longer than the 5-minute subagent cache TTL (docs/ddflow/research/token-spend.md section 3.3).
Claude Code 2.1.242 or later can raise it to 1 hour, but ddflow cannot flip the harness's own
environment -- `doctor` reports the effective TTL and warns while subagents still run on the
5-minute default, so a host that launches Claude Code with the setting already in its env block
is not warned and a host that does not is named.

Precedence is read from the docs, not invented (agent-token-features.md 2.1, token-spend.md 5):
``FORCE_PROMPT_CACHING_5M=1`` overrides everything (forces 5m), then the subagent-specific knob
``CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`` / setting ``subagentPromptCacheTtl``, then
``ENABLE_PROMPT_CACHING_1H=1`` (raises both main and subagent to 1h); otherwise subagents
default to 5m even on a subscription. A 1-hour write costs 2x vs 1.25x, so it pays off whenever
the gap between calls exceeds 5 minutes, which is the normal case here. The main-conversation
knobs ``CLAUDE_CODE_PROMPT_CACHE_TTL`` / ``promptCacheTtl`` do NOT govern subagents and are
intentionally not consulted.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

#: The env/setting names the docs attribute to the SUBAGENT cache TTL and its overrides.
SUBAGENT_TTL_ENV = "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"
SUBAGENT_TTL_SETTING = "subagentPromptCacheTtl"
ENABLE_1H_ENV = "ENABLE_PROMPT_CACHING_1H"
FORCE_5M_ENV = "FORCE_PROMPT_CACHING_5M"

#: The first Claude Code line that knows the subagent 1-hour knobs (docs: 2.1.242).
MIN_1H_VERSION = (2, 1, 242)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _env_mapping(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _is_on(value: str | None) -> bool:
    """Truthy as the docs use the flag names: ``1`` or ``true`` (case-blind) enables, an empty
    or unset value does nothing."""
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def parse_version(version: str | None) -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` from ``claude --version`` output, or ``None`` when the string
    carries no version (so "unknown", never a guess)."""
    if not version:
        return None
    m = _VERSION_RE.search(version)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def supports_1h(version: str | None) -> bool | None:
    """Whether ``version`` can set the subagent cache TTL to 1 hour. ``True``/``False`` from a
    parsed version >= 2.1.242, ``None`` when it could not be told."""
    parsed = parse_version(version)
    return None if parsed is None else parsed >= MIN_1H_VERSION


def read_settings(repo: Path) -> Mapping[str, Any]:
    """The Claude Code settings relevant to the cache TTL, best-effort: the project
    ``.claude/settings.json`` merged over the user's ``~/.claude/settings.json`` (the project
    wins, matching Claude Code's own merge). Unreadable is ``{}`` -- a report must not fail on
    its own probing, and a missing setting reads the same as an absent one (the env and the
    doctor note, not the settings read, carry the warning)."""
    merged: dict[str, Any] = {}
    paths: list[Path] = []
    try:
        home = Path.home()
    except RuntimeError:  # HOME unset: no user settings to read, never an error
        home = None
    if home is not None:
        paths.append(home / ".claude" / "settings.json")
    # Claude Code's own merge: user settings, then project settings, then the project-local
    # `.claude/settings.local.json` on top; a later file wins for the same key.
    paths += [repo / ".claude" / "settings.json", repo / ".claude" / "settings.local.json"]
    for path in paths:
        try:
            if path.is_file():
                merged.update(json_load(path))
        except Exception:
            continue
    return merged


def json_load(path: Path) -> dict[str, Any]:
    """``json.load`` kept as a module-level function so tests can stub it without touching the
    real file system."""
    import json

    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def claude_version(timeout_s: int = 5) -> str | None:
    """``claude --version`` output on this host, or ``None`` when the binary is absent or does
    not answer (unknown, never a guess). Cheap: a local binary that prints one line."""
    if shutil.which("claude") is None:
        return None
    try:
        proc = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=timeout_s
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


@dataclass(frozen=True)
class SubagentTTL:
    """The effective subagent cache TTL and why it is what it is."""

    ttl: str  # "5m" | "1h"
    source: str  # which knob decided it (see `effective_subagent_ttl`)
    forced: bool = False  # FORCE_PROMPT_CACHING_5M in effect
    version: tuple[int, int, int] | None = None  # None = unknown
    ignored_1h: bool = False  # a 1h knob was set, but this Claude Code is too old to honor it

    @property
    def seconds(self) -> int:
        return 3600 if self.ttl == "1h" else 300

    @property
    def one_hour_available(self) -> bool | None:
        """Whether the host's Claude Code can even express 1 hour: ``None`` when the version
        could not be told (still reported, never a pass), ``False`` when it is older than the
        first release that knows the knob."""
        if self.version is None:
            return None
        return self.version >= MIN_1H_VERSION

    @property
    def on_five_minutes(self) -> bool:
        return self.ttl == "5m"


def effective_subagent_ttl(
    env: Mapping[str, str] | None = None,
    settings: Mapping[str, Any] | None = None,
    version: str | None = None,
) -> SubagentTTL:
    """The effective subagent cache TTL from the environment and Claude Code settings, in the
    documented precedence (highest first):

    1. ``FORCE_PROMPT_CACHING_5M=1`` -- forces 5m over everything else.
    2. ``CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`` (env) -- "1h" or "5m".
    3. ``subagentPromptCacheTtl`` (settings) -- when the env is unset.
    4. ``ENABLE_PROMPT_CACHING_1H=1`` -- raises both main and subagent to 1h.
    5. default -- subagents run on 5m even on a subscription.
    """
    env = _env_mapping(env)
    settings = settings if settings is not None else {}
    version_tuple = parse_version(version)
    too_old = version_tuple is not None and version_tuple < MIN_1H_VERSION

    # A settings `env` block names env vars Claude Code sets for the process it launches; a
    # var already in the process env wins over it (D-agent-token-features, settings vs shell).
    settings_env = settings.get("env")
    effective_env = (
        {**settings_env, **dict(env)} if isinstance(settings_env, dict) else dict(env)
    )

    if _is_on(effective_env.get(FORCE_5M_ENV)):
        return SubagentTTL("5m", "forced_5m", forced=True, version=version_tuple)

    subagent_env = (effective_env.get(SUBAGENT_TTL_ENV) or "").strip().lower()
    if subagent_env:
        ttl = "1h" if subagent_env.startswith("1h") else "5m"
        if ttl == "1h" and too_old:  # the knob exists but this Claude Code ignores it
            return SubagentTTL("5m", "subagent_env", version=version_tuple, ignored_1h=True)
        return SubagentTTL(ttl, "subagent_env", version=version_tuple)

    subagent_setting = settings.get(SUBAGENT_TTL_SETTING)
    subagent_value = (
        str(subagent_setting).strip().lower() if subagent_setting is not None else ""
    )
    if subagent_value:
        ttl = "1h" if subagent_value.startswith("1h") else "5m"
        if ttl == "1h" and too_old:
            return SubagentTTL("5m", "subagent_setting", version=version_tuple, ignored_1h=True)
        return SubagentTTL(ttl, "subagent_setting", version=version_tuple)

    if _is_on(effective_env.get(ENABLE_1H_ENV)):
        if too_old:
            return SubagentTTL("5m", "enable_1h", version=version_tuple, ignored_1h=True)
        return SubagentTTL("1h", "enable_1h", version=version_tuple)

    return SubagentTTL("5m", "default", version=version_tuple)


def knob_name(source: str) -> str:
    """The env/setting name to cite for a source, so the doctor note names the knob that won,
    not an opaque enum."""
    return {
        "subagent_env": SUBAGENT_TTL_ENV,
        "subagent_setting": SUBAGENT_TTL_SETTING,
        "enable_1h": ENABLE_1H_ENV,
        "forced_5m": FORCE_5M_ENV,
    }.get(source, source)


def doctor_notes(repo: Path, cfg=None) -> list[str]:
    """The ``ddflow doctor`` notes for the subagent cache TTL: what the effective TTL is and,
    while subagents run on 5 minutes, the one-line remedy. ``cfg`` is unused today (the knobs
    are the harness's env/settings, not a ddflow config section) and is accepted so the caller
    can pass its own ``Config`` uniformly."""
    verdict = effective_subagent_ttl(os.environ, read_settings(repo), claude_version())
    if verdict.ttl == "1h":
        if verdict.version is None:
            return [
                f"subagent cache TTL: 1h (via {knob_name(verdict.source)}) -- Claude Code "
                "version unknown: verify it is 2.1.242 or later, which first honors the knob"
            ]
        return [f"subagent cache TTL: 1h (via {knob_name(verdict.source)})"]
    return [f"subagent cache TTL: 5m -- {_five_minute_remedy(verdict)}"]


def _five_minute_remedy(v: SubagentTTL) -> str:
    """What to do about a 5-minute TTL. When the host's Claude Code is too old to express 1
    hour, the remedy is "upgrade", reported as unavailable rather than as a setting ddflow can
    emit (UNAVAILABLE not pass)."""
    if v.forced:
        if v.one_hour_available is False:
            return (
                f"forced by {FORCE_5M_ENV}, and Claude Code {'.'.join(map(str, v.version))} is "
                f"older than 2.1.242: unsetting the force then setting {SUBAGENT_TTL_ENV}=1h "
                "would still leave subagents on 5m until it is upgraded"
            )
        if v.one_hour_available is None:
            return (
                f"forced by {FORCE_5M_ENV}: unset it, then set {SUBAGENT_TTL_ENV}=1h "
                "(verify Claude Code 2.1.242 or later)"
            )
        return (
            f"forced by {FORCE_5M_ENV}: unset it, then set {SUBAGENT_TTL_ENV}=1h"
        )
    if v.one_hour_available is False:
        ignored = f": {knob_name(v.source)} is set to 1h but ignored" if v.ignored_1h else ""
        return (
            f"Claude Code {'.'.join(map(str, v.version))} is older than 2.1.242: the 1-hour "
            f"subagent cache TTL knob ({SUBAGENT_TTL_ENV}) is unavailable until it is upgraded"
            f"{ignored}"
        )
    if v.one_hour_available is None:
        return (
            f"Claude Code version unknown: set {SUBAGENT_TTL_ENV}=1h (or settings "
            f"{SUBAGENT_TTL_SETTING}) to raise it from 5m"
        )
    return f"set {SUBAGENT_TTL_ENV}=1h (or settings {SUBAGENT_TTL_SETTING}) to raise it from 5m"
