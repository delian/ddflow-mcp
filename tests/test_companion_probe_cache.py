"""A cached companion probe keeps its own timestamp, so it expires (Bc87ca3936a).

`scan` wrote the cache back as `dict(cached)` plus the new results, re-stamping every
carried-over entry with the current time: as long as any companion was probed within the
TTL, a stale result never expired.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import companions as CO


def test_an_entry_carried_over_keeps_its_own_timestamp(tmp_path, monkeypatch):
    first = {c.id for c in CO.load(tmp_path)}
    assert len(first) >= 2, "fixture: needs two shipped companions"
    old_id, *_ = sorted(first)
    old_at = time.time() - 50
    path = CO._cache_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({old_id: {"installed": True, "detail": "x", "at": old_at}}))
    # every other companion is probed now (and found absent, a definite answer)
    monkeypatch.setattr(CO, "is_installed", lambda c: (False, "not on PATH"))
    CO.scan(tmp_path, ttl_s=100)
    raw = json.loads(path.read_text())
    assert raw[old_id]["at"] == old_at, "the carried-over entry was re-stamped"
    assert len(raw) == len(first)
