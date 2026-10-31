"""The append-only log — the one place events are written and read.

Serialised appends under `fcntl.flock` with an `fsync`, and per-agent shards so two
agents never write the same file. The Lamport clock is the max over every parsed event,
not each shard's tail: a shard two clones wrote under one id is not monotone (B190).

Reading exploits the same append-only property: `read_all` re-parses only the bytes
APPENDED since the last read, because parsing is 84% of a read's cost (97 ms of 115 ms
at 20,000 events) and a line already parsed cannot have changed. This is why the log is
never compacted — see `tests/test_log_read_cache.py` for the probe that declined it.

The `Event` record itself lives in `core.events`: it is a value with no I/O, and the
domain layer needs it without needing this.
"""

from __future__ import annotations

import os as os
import time as time

from ...core import digest as D  # noqa: F401  # re-exported as the historical ``log.D`` alias
from ...core.events import (
    PROVENANCE_KINDS as PROVENANCE_KINDS,
)
from ...core.events import (
    SCHEMA_VERSION as SCHEMA_VERSION,
)
from ...core.events import (
    Event as Event,
)
from ...core.events import (
    SkewRefused as SkewRefused,
)
from ...core.events import (
    canonical as canonical,
)
from ...core.events import (
    utcnow as utcnow,
)
from ._agent import (
    _AGENT_ID_CACHE as _AGENT_ID_CACHE,
)
from ._agent import (
    _NO_RANK as _NO_RANK,
)
from ._agent import (
    _SUFFIX as _SUFFIX,
)
from ._agent import (
    _TREE_KINDS as _TREE_KINDS,
)
from ._agent import (
    CLONE_ID_FILE as CLONE_ID_FILE,
)
from ._agent import (
    _clone_suffix as _clone_suffix,
)
from ._agent import (
    _common_dir as _common_dir,
)
from ._agent import (
    _linked_tree_here as _linked_tree_here,
)
from ._agent import (
    _recovered_suffix as _recovered_suffix,
)
from ._agent import (
    _shard_evidence as _shard_evidence,
)
from ._agent import (
    _toplevel as _toplevel,
)
from ._agent import (
    bare_agent_id as bare_agent_id,
)
from ._agent import (
    clone_suffix_since as clone_suffix_since,
)
from ._agent import (
    default_agent_id as default_agent_id,
)
from ._agent import (
    tree_agent_ids as tree_agent_ids,
)
from ._eventlog import (
    _STAMP_EXEMPT as _STAMP_EXEMPT,
)
from ._eventlog import (
    APPEND_HOOKS as APPEND_HOOKS,
)
from ._eventlog import (
    EventLog as EventLog,
)
from ._guard import _GuardMixin as _GuardMixin
from ._lock import (
    _CMD_SHOWN as _CMD_SHOWN,
)
from ._lock import (
    _CMD_TAIL as _CMD_TAIL,
)
from ._lock import (
    _HELD as _HELD,
)
from ._lock import (
    _HOLDER_SCAN_S as _HOLDER_SCAN_S,
)
from ._lock import (
    _HOLDER_SCANS_MAX as _HOLDER_SCANS_MAX,
)
from ._lock import (
    _LOCK_FIELDS as _LOCK_FIELDS,
)
from ._lock import (
    _PYTHON as _PYTHON,
)
from ._lock import (
    _describe_pid as _describe_pid,
)
from ._lock import (
    _flock as _flock,
)
from ._lock import (
    _held_key as _held_key,
)
from ._lock import (
    _holder_note as _holder_note,
)
from ._lock import (
    _locks_holder as _locks_holder,
)
from ._lock import (
    _still_locked as _still_locked,
)
from ._parse import (
    _EVENT_FIELDS as _EVENT_FIELDS,
)
from ._parse import (
    _LINE_START as _LINE_START,
)
from ._parse import (
    _MAX_INCREMENTAL as _MAX_INCREMENTAL,
)
from ._parse import (
    _MERGED as _MERGED,
)
from ._parse import (
    _ORDER_ARITY as _ORDER_ARITY,
)
from ._parse import (
    _PARSE_CACHE as _PARSE_CACHE,
)
from ._parse import (
    _READ_CHUNK as _READ_CHUNK,
)
from ._parse import (
    _SNAP_COVERED as _SNAP_COVERED,
)
from ._parse import (
    _SNAP_ORDER as _SNAP_ORDER,
)
from ._parse import (
    _SNAPSHOT_ARITY as _SNAPSHOT_ARITY,
)
from ._parse import (
    _SNAPSHOT_SLOT as _SNAPSHOT_SLOT,
)
from ._parse import (
    _SNAPSHOTS as _SNAPSHOTS,
)
from ._parse import (
    SNAPSHOT_ENV as SNAPSHOT_ENV,
)
from ._parse import (
    SNAPSHOT_FILE as SNAPSHOT_FILE,
)
from ._parse import (
    SNAPSHOT_FORMAT as SNAPSHOT_FORMAT,
)
from ._parse import (
    _cached_events as _cached_events,
)
from ._parse import (
    _Delta as _Delta,
)
from ._parse import (
    _digest as _digest,
)
from ._parse import (
    _event_tuple as _event_tuple,
)
from ._parse import (
    _gated_read as _gated_read,
)
from ._parse import (
    _license as _license,
)
from ._parse import (
    _license_cached as _license_cached,
)
from ._parse import (
    _parse_event as _parse_event,
)
from ._parse import (
    _parse_lines as _parse_lines,
)
from ._parse import (
    _Parsed as _Parsed,
)
from ._parse import (
    _parser_stamp as _parser_stamp,
)
from ._parse import (
    _recover_glued as _recover_glued,
)
from ._parse import (
    _snapshot_frame as _snapshot_frame,
)
from ._parse import (
    _snapshot_objects as _snapshot_objects,
)
from ._parse import (
    _sorted_unique as _sorted_unique,
)
from ._parse import (
    clear_parse_cache as clear_parse_cache,
)
from ._read import _last_line as _last_line
from ._read import _ReadMixin as _ReadMixin
from ._skew import (
    _SKEW_WARNED as _SKEW_WARNED,
)
from ._skew import (
    SEEN_MARKER as SEEN_MARKER,
)
from ._skew import (
    SKEW_NOTICE_MARKER as SKEW_NOTICE_MARKER,
)
from ._skew import (
    LogMark as LogMark,
)
from ._skew import (
    _marker_is_new as _marker_is_new,
)
from ._skew import (
    _notice_due as _notice_due,
)
from ._skew import (
    _warn_patch_skew_once as _warn_patch_skew_once,
)
from ._skew import (
    _warn_skew_once as _warn_skew_once,
)
from ._skew import (
    _write_seen_marker as _write_seen_marker,
)
from ._skew import (
    capability_message as capability_message,
)
from ._skew import (
    install_kind as install_kind,
)
from ._skew import (
    running_version as running_version,
)
from ._skew import (
    skew_message as skew_message,
)
from ._skew import (
    upgrade_remedy as upgrade_remedy,
)
from ._skew import (
    version_known as version_known,
)

__all__ = [
    "PROVENANCE_KINDS",
    "SCHEMA_VERSION",
    "Event",
    "EventLog",
    "SkewRefused",
    "canonical",
    "clear_parse_cache",
    "default_agent_id",
    "utcnow",
]
