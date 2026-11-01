"""Version and skew-guard helpers, and the ``LogMark`` an ``EventLog.mark()`` returns."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from ...config_sections import _compat as CV
from ...core import version as V
from ...core.events import utcnow, version_key
from .. import fsio

#: The local, git-ignored marker: the last ddflow version THIS machine acted under.
SEEN_MARKER = Path(".ddflow") / "local" / "seen.json"


def running_version() -> str:
    """The version of the code that is running (read at call time, so a test can set it)."""
    return V.running()


def version_known(version: str) -> bool:
    """A version worth stamping: one that parses. A source tree reporting "unknown" must not
    stamp a log with a version that compares as nothing."""
    return bool(version_key(version))


def install_kind() -> str:
    """How this ddflow is installed, cheaply: `source-tree` when the package lives in a
    checkout, else `installed`. (`services.install_info` is richer and runs git; a stamp
    written on every first write must not.)"""
    return "source-tree" if CV.is_source_tree() else "installed"


def upgrade_remedy(highest: str = "") -> str:
    """`config_sections._compat.upgrade_advice` for how this ddflow is installed, as the
    sentence a refusal opens its remedy with: a source checkout merges main, an installed
    ddflow upgrades its package (``highest`` is the version needed, "" when unknown)."""
    text = CV.upgrade_advice(highest, install_kind())
    if highest and highest not in text:  # a source checkout's advice names no version
        text += f" (ddflow >= {highest} is needed)"
    return text[:1].upper() + text[1:]


def skew_message(
    version: str,
    highest: str,
    by: str = "",
    *,
    log_format: int = 0,
    format_level: int = 0,
    format_only: bool = False,
    format_version: str = "",
    format_by: str = "",
) -> str:
    """The refusal for a write by a ddflow behind the log. ``format_only`` is a log whose
    data FORMAT is ahead although this ddflow's version is not older (`StampFacts.format_skewed`):
    it names the stamp that carries the format and asks for a release at that level, since
    "upgrade to >= <this version>" would already be satisfied."""
    who = f" (stamped by {by})" if by else ""
    if format_only and log_format > format_level > 0:
        fwho = f" (stamped by {format_by})" if format_by else ""
        head = (
            f"REFUSED: this project's log has been worked on by ddflow {format_version or highest}"
            f"{fwho} at data format level {log_format}, and this ddflow ({version}) writes level "
            f"{format_level}, which is older: writing now could drop or misread what the newer "
            f"one recorded."
        )
        remedy = (
            f"{upgrade_remedy()} (the release needed writes data format level {log_format} or "
            f"higher; ddflow {format_version or highest} did)"
        )
    else:
        head = (
            f"REFUSED: this project's log has been worked on by ddflow {highest}{who}, and "
            f"this ddflow is {version}, which is older: writing now could drop or misread "
            f"what the newer one recorded."
        )
        if log_format > format_level > 0:
            head += f" Its data format level ({log_format}) is ahead of this ddflow's too ({format_level})."
        remedy = upgrade_remedy(highest)
    return (
        f"{head} {remedy} and retry. Reads still work. If you cannot upgrade, ask the user; only if the "
        f'user insists, rerun with --allow-older-version --reason "<why>" (MCP: the '
        f"allow_older_version argument carrying the reason). That override is recorded "
        f"(skew.overridden), marks this session's events as written by an older ddflow, and "
        f"covers this session only. [upgrade].skew = warn or off turns the guard down."
    )


def capability_message(name: str, since: str, kind: str, version: str, by: str = "") -> str:
    """The refusal for ONE write whose capability this ddflow lacks (decision D-compat 2):
    it names the capability, the kind refused and the release that provides it, and says
    everything else proceeds."""
    who = f" (recorded by {by})" if by else ""
    return (
        f"REFUSED: this write ({kind}) needs the `{name}` capability, which this project's "
        f"log records{who} and ddflow {version} does not have: writing it could misread or "
        f"drop what a newer ddflow recorded under it. {upgrade_remedy(since)} "
        f"and retry. Only writes of this kind are refused; everything else proceeds. If "
        f'you cannot upgrade, ask the user; `[upgrade].skew = "off"` is theirs to set.'
    )


_SKEW_WARNED: set[tuple[str, str, int, str]] = set()


SKEW_NOTICE_MARKER = Path(".ddflow") / "local" / "skew-notice.json"


def _notice_due(root: Path | None, key: tuple[str, str, int], mode: str) -> bool:
    """Should the skew notice for ``key`` be said now? ``off`` never; ``always`` once per
    process; ``once`` (default) once per key per machine and project, remembered in a
    git-ignored marker so the next CLI process (one per call) stays quiet
    (B-path-ddflow-version-skew-noise). A marker that cannot be read or written only
    means the notice may repeat."""
    if mode == "off":
        return False
    process_key = (
        *key,
        str(Path(root).resolve()) if root else "",
    )  # two projects in one process each hear it
    if process_key in _SKEW_WARNED:
        return False
    _SKEW_WARNED.add(process_key)
    if mode == "always" or root is None:
        return True
    return _marker_is_new(Path(root) / SKEW_NOTICE_MARKER, "|".join(map(str, key)))


def _marker_is_new(path: Path, tag: str) -> bool:
    """Record ``tag`` in the marker at ``path``; False when it was already there. The read
    and the write are one step under a lock, so two processes do not lose each other's tag;
    a marker that cannot be read or written is treated as new (the notice repeats)."""
    try:
        fsio.ensure_ignored_dir(path.parent)
        with fsio.file_lock(path.with_suffix(".lock"), 5.0):
            try:
                seen = json.loads(path.read_text("utf-8")).get("seen", [])
            except (OSError, ValueError, AttributeError):
                seen = []
            if not isinstance(seen, list):  # parses but is not ours: say it again
                seen = []
            if tag in seen:
                return False
            fsio.atomic_write(path, json.dumps({"seen": [*seen, tag][-20:]}) + "\n")
    except (OSError, fsio.LockTimeout):
        pass
    return True


def _warn_skew_once(
    version: str,
    highest: str,
    log_format: int = 0,
    format_level: int = 0,
    *,
    root: Path | None = None,
    mode: str = "once",
) -> None:
    """Say once per (version, log version, log format) that a policy of `warn` writes into a
    log that is ahead. The format is part of the key, so a later, higher format is said again."""
    if not _notice_due(root, (version, highest, log_format), mode):
        return
    ahead = (
        f" (data format level {log_format}, this ddflow writes {format_level})"
        if (log_format > format_level > 0)
        else ""
    )
    print(
        f"ddflow: this log has been worked on by ddflow {highest}{ahead}; this ddflow is "
        f"{version}. Writing anyway ([upgrade].skew = warn). {upgrade_remedy(highest)}; "
        f"[upgrade].skew_notice = off silences this.",
        file=sys.stderr,
    )


def _warn_patch_skew_once(
    version: str, highest: str, *, root: Path | None = None, mode: str = "once"
) -> None:
    """Say once (per machine and project, see `_notice_due`) that a PATCH skew -- same data
    format, same minor, no capability gap -- was let through the guard. The event carries the
    older-ddflow mark."""
    if not _notice_due(root, (version, highest, -1), mode):
        return
    print(
        f"ddflow: this log has been worked on by ddflow {highest}, a newer patch release; this "
        f"ddflow is {version}. Same data format, so writing anyway (patch skew); its events "
        f"are marked as written by an older ddflow. {upgrade_remedy(highest)}. Said once; "
        f"[upgrade].skew_notice = off silences it, always repeats it.",
        file=sys.stderr,
    )


def _write_seen_marker(root: Path, version: str) -> None:
    """Record, machine-locally, the version this machine last acted under. Best effort: a
    marker that cannot be written costs only the once-per-version upgrade notice."""
    path = Path(root) / SEEN_MARKER
    try:
        fsio.ensure_ignored_dir(path.parent)
        fsio.atomic_write(path, json.dumps({"version": version, "at": utcnow()}) + "\n")
    except OSError:
        pass


@dataclass(frozen=True, slots=True)
class LogMark:
    """What `EventLog.mark()` saw: `(shard name, size in bytes, clock on its last line)`
    per shard, in name order. Equal marks mean nothing was appended; compare with `==`."""

    shards: tuple[tuple[str, int, int], ...]

    @property
    def count(self) -> int:
        return len(self.shards)

    @property
    def lamport(self) -> int:
        """The highest TAIL clock -- not the highest clock: after a union merge of a shard
        with two writers they differ (see `EventLog._highest_lamport`). Harmless for a
        fingerprint, since the merge changed the size; do not take a clock from it."""
        return max((high for _n, _s, high in self.shards), default=0)

    @property
    def bytes(self) -> int:
        return sum(size for _n, size, _h in self.shards)
