"""Bug Ba2df74d558: a LAN address reached the committed log through a lookup field.

`redact_event_data` left the fields named in `LOG_VERBATIM_NAMES` (url, path, worktree,
branch, tags, globs, subject ...) untouched, because redacting an id or a sha would break
the lookup. But a URL or a path can name a private host, and the log is pushed. Those fields
now get the network pass only (private addresses and private host names), never the rest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.config_sections.session import SessionConfig
from ddflow.core import redact as R
from ddflow.infra.log import EventLog

LAN = ".".join(("10", "77", "3", "91"))  # built, so no private host is committed text
HOST = "nas" + ".home.arpa"  # built, like LAN
SHA = "0123456789abcdef0123456789abcdef01234567"

CASES = [
    ("pr.opened", "url", f"http://{LAN}:3000/o/r/pulls/4"),
    ("lease.acquired", "worktree", f"/srv/{HOST}/wt"),
    ("lease.acquired", "branch", f"ddflow/{LAN}"),
    ("task.added", "tags", [f"host-{LAN}", "ok"]),
    ("task.added", "globs", [f"mnt/{HOST}/**"]),
]


def _shard(root: Path) -> str:
    return "".join(p.read_text() for p in (root / ".ddflow" / "events").glob("*.jsonl"))


@pytest.mark.parametrize(("kind", "field", "value"), CASES)
def test_a_lookup_field_carries_no_private_host(kind, field, value, tmp_path):
    log = EventLog(tmp_path, "T")
    log.stamp = False
    log.append(kind, "x", {field: value})
    shard = _shard(tmp_path)
    assert LAN not in shard and HOST not in shard, f"{kind}.{field} committed a private host"
    assert "[REDACTED" in shard


def test_a_lookup_that_names_no_host_is_unchanged(tmp_path):
    log = EventLog(tmp_path, "T")
    log.stamp = False
    data = {"holder": "T", "worktree": "/work/proj/.ddflow/worktrees/x", "branch": "ddflow/x"}
    log.append("lease.acquired", "x", data)
    log.append(
        "worktree.merged", "x", {"branch_head": SHA, "tree": SHA, "url": "https://example.org/a"}
    )
    shard = _shard(tmp_path)
    assert "/work/proj/.ddflow/worktrees/x" in shard and SHA in shard
    assert "https://example.org/a" in shard
    assert "[REDACTED" not in shard


def test_the_knob_switches_it_off(tmp_path):
    assert SessionConfig().redact_lookup_network is True
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text("[session]\nredact_lookup_network = false\n")
    log = EventLog(tmp_path, "T")
    log.stamp = False
    log.append("pr.opened", "x", {"url": f"http://{LAN}/x"})
    log.append("bug.found", "y", {"summary": f"at {LAN}"})  # free text is redacted regardless
    shard = _shard(tmp_path)
    assert f"http://{LAN}/x" in shard
    assert f"at {LAN}" not in shard


def test_a_loopback_endpoint_is_not_a_leak(tmp_path):
    local = "http://" + ".".join(("127", "0", "0", "1")) + ":8000/v1"
    log = EventLog(tmp_path, "T")
    log.stamp = False
    log.append("pr.opened", "x", {"url": local})
    assert local in _shard(tmp_path)


def test_a_verb_word_that_only_looks_like_a_host_is_written_verbatim(tmp_path):
    """Bug B31f83a9d7b: `verb` is compared byte-for-byte with the canonical verb, so the
    network pass must not rewrite a gated-command verb whose site ends in `.local`: the verb
    is a word, never a URL or a path (Ba2df74d558)."""
    verb = "export.machine" + ".local"  # built, so no private host is committed text
    log = EventLog(tmp_path, "T")
    log.stamp = False
    log.append("ask.requested", "x", {"verb": verb})
    log.append("ask.consumed", "x", {"verb": verb, "by": "T"})
    shard = _shard(tmp_path)
    assert verb in shard, "a `.local` gated-command verb was not stored as typed"
    assert "[REDACTED:host]" not in shard


def test_no_network_fields_are_declared_verbatim():
    """`LOG_NO_NETWORK_FIELDS` only narrows the network pass on fields the vocabulary already
    declares verbatim, so it can never exempt a field that would otherwise be redacted text."""
    assert set(R.LOG_NO_NETWORK_FIELDS) <= set(R.LOG_VERBATIM_FIELDS)
