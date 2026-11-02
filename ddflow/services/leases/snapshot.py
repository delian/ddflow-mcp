"""The lease snapshot: a small machine-local file kept current on every lease event.

Writing side of `.ddflow/local/lease-snapshot.json` (reading side: `enforce.hooksnap`, which a
per-edit hook can import cheaply). It is a HINT for the hook, never the authority: the
commit-time check still folds the log. So it is projected event by event, without the fold:

* ``lease.acquired`` sets the item's entry; ``lease.renewed`` moves its clock (and globs);
* ``lease.released``/``lease.expired`` and the end of an item (``item.completed``,
  ``item.abandoned``, a removed task) drop it.

A missing, torn or unreadable file is REBUILT with one fold of the log (the rare case: first
lease event after the file was lost), so the snapshot heals itself on the next lease event and
no daemon is needed. A lease of another clone is timed by its own clock here (the fold's
`leaseclock.localize` is not applied); a skewed clock can make the hook's picture wrong by the
skew, and the commit-time check stays the judge. Registered on the log's append hooks, so every
writer of a lease event updates it under the log lock. Never raises: a failed update leaves the
old file, which ages out (`[locks].snapshot_max_age_s`) into the hook's fail-open notice.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.admission import new_file_globs, shared_globs
from ...core.model import fold
from ...infra.localstore import LocalStore, StoreUnreadable
from ...infra.log import APPEND_HOOKS

#: Event kinds that can change who holds what.
WATCHED = frozenset(
    {
        "lease.acquired",
        "lease.renewed",
        "lease.released",
        "lease.expired",
        "item.completed",
        "item.abandoned",
        "task.removed",
        "phase.removed",
    }
)
_ENDS = ("lease.released", "lease.expired", "item.completed", "item.abandoned")


def _entry(d: dict[str, Any], agent: str) -> dict[str, Any]:
    at = float(d.get("at", 0.0))
    return {
        "holder": d.get("holder", agent),
        "globs": list(d.get("globs", [])),
        "renewed_at": at,
        "ttl_s": int(d.get("ttl_s", 1800)),
        "worktree": d.get("worktree", ""),
    }


def project(
    leases: dict[str, dict[str, Any]], kind: str, subject: str, d: dict, agent: str
) -> None:
    """Apply one lease-relevant event to ``leases`` (item id -> entry), in place."""
    if kind == "lease.acquired":
        leases[subject] = _entry(d, agent)
    elif kind == "lease.renewed":
        cur = leases.get(subject)
        if cur is None or d.get("holder", cur["holder"]) != cur["holder"]:
            return  # nothing known to renew, or a former holder's late renewal
        at = float(d.get("at", cur["renewed_at"]))
        cur["renewed_at"] = max(at, cur["renewed_at"])
        if "globs" in d:
            cur["globs"] = list(d["globs"])
        if "worktree" in d:
            cur["worktree"] = d["worktree"]
    elif kind in _ENDS or kind.endswith(".removed"):
        cur = leases.get(subject)
        if cur is not None and d.get("holder", cur["holder"]) == cur["holder"]:
            leases.pop(subject)


def from_log(log: Any, cfg: Config, now: float) -> dict[str, dict[str, Any]]:
    """The live leases of unfinished items, by one fold of ``log`` (the rebuild)."""
    st = fold(log.read_all(), strict=False)
    out: dict[str, dict[str, Any]] = {}
    for item_id, lz in st.active_leases(now, cfg.lease.grace_s).items():
        if item_id in st.items and st.items[item_id].terminal:
            continue
        out[item_id] = {
            "holder": lz.holder,
            "globs": list(lz.globs),
            "renewed_at": lz.renewed_at,
            "ttl_s": lz.ttl_s,
            "worktree": lz.worktree,
        }
    return out


def _doc(cur: Any, leases: dict[str, dict[str, Any]], cfg: Config, now: float) -> dict[str, Any]:
    gen = cur.get("generation", 0) if isinstance(cur, dict) else 0
    return {
        "generation": int(gen) + 1,
        "written_at": now,
        "max_age_s": cfg.locks.snapshot_max_age_s,
        "grace_s": cfg.lease.grace_s,
        "leases": leases,
        "shared": list(shared_globs(cfg)),
        "new_files": list(new_file_globs(cfg)),
        "agents": _agents(leases),
    }


def _agents(leases: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Holder -> the items it holds: the caller identity map a hook asks 'who am I'."""
    out: dict[str, list[str]] = {}
    for item_id, lz in sorted(leases.items()):
        out.setdefault(str(lz["holder"]), []).append(item_id)
    return out


def apply(log: Any, kind: str, subject: str, data: dict[str, Any], agent: str) -> bool:
    """Bring the snapshot up to date with one event. True when it was written."""
    from ..enforce import hooksnap as H  # late: the enforce package imports this one's parent

    cfg = Config.load(log.root)
    if not cfg.locks.snapshot:
        return False
    store = LocalStore(Path(log.root) / ".ddflow" / "local")

    def step(cur: Any) -> dict[str, Any]:
        now = time.time()
        ok = isinstance(cur, dict) and isinstance(cur.get("leases"), dict)
        if ok:
            leases = {k: dict(v) for k, v in cur["leases"].items()}
            project(leases, kind, subject, data, agent)
        else:
            leases = from_log(log, cfg, now)  # includes this event: it is already appended
        return _doc(cur, leases, cfg, now)

    for attempt in (0, 1):
        try:
            store.update(H.NAME, step, schema=H.SCHEMA, derived=True)
            return True
        except StoreUnreadable:
            if attempt:
                return False
            store.remove(H.NAME)  # torn: rebuilt on the next pass
    return False


def appended(log: Any, event: Any, before: int, after: int) -> None:
    """The log's append hook: refresh the snapshot after a lease-relevant event."""
    if event.kind in WATCHED:
        with contextlib.suppress(Exception):
            apply(log, event.kind, event.subject, event.data, event.agent)


if appended not in APPEND_HOOKS:
    APPEND_HOOKS.append(appended)
