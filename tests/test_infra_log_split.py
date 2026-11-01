"""The ``ddflow.infra.log`` split kept every name the single module defined.

The 2,213-line ``ddflow/infra/log.py`` became a package without losing a name: every
symbol the module defined at top level is still reachable as ``ddflow.infra.log.<name>``.
The split hand-maintained a ~250-name eager re-export index and lost ``_Merged`` once
(``ddflow.infra.log._read`` imports it, but the package ``__init__`` forgot it); this pins
the defined set so the next name no caller may lose cannot regress silently (critic #1,
lesson L1167d440f5: assert ``DEFINED <= package names``, never the exact set).
"""

from __future__ import annotations

import ddflow.infra.log as log_pkg

#: What the single module defined at top level before the split. The ``T`` TypeVar is
#: left out: it is a typing-only alias, not a runtime name a caller imports from the module.
DEFINED = """
    APPEND_HOOKS CLONE_ID_FILE EventLog LogMark SEEN_MARKER SKEW_NOTICE_MARKER
    SNAPSHOT_ENV SNAPSHOT_FILE SNAPSHOT_FORMAT _AGENT_ID_CACHE _CMD_SHOWN _CMD_TAIL
    _Delta _EVENT_FIELDS _HELD _HOLDER_SCANS_MAX _HOLDER_SCAN_S _LINE_START _LOCK_FIELDS
    _MAX_INCREMENTAL _MERGED _Merged _NO_RANK _ORDER_ARITY _PARSE_CACHE _PYTHON _Parsed
    _READ_CHUNK _SKEW_WARNED _SNAPSHOTS _SNAPSHOT_ARITY _SNAPSHOT_SLOT _SNAP_COVERED
    _SNAP_ORDER _STAMP_EXEMPT _SUFFIX _TREE_KINDS _cached_events _clone_suffix _common_dir
    _describe_pid _digest _event_tuple _flock _gated_read _held_key _holder_note _last_line
    _license _license_cached _linked_tree_here _locks_holder _marker_is_new _notice_due
    _parse_event _parse_lines _parser_stamp _recover_glued _recovered_suffix _shard_evidence
    _snapshot_frame _snapshot_objects _sorted_unique _still_locked _toplevel
    _warn_patch_skew_once _warn_skew_once _write_seen_marker bare_agent_id capability_message
    clear_parse_cache clone_suffix_since default_agent_id install_kind running_version
    skew_message tree_agent_ids upgrade_remedy version_known
""".split()


def test_no_name_the_single_module_defined_is_lost():
    missing = [n for n in DEFINED if not hasattr(log_pkg, n)]
    assert not missing, missing
