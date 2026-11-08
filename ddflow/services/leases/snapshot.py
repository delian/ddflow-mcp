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
from pathlib import Path
from typing import Any

from ...config import Config
from ...core import leasesnap as H
from ...core.admission import new_file_globs, shared_globs
from ...core.model import DEFAULT_LEASE_TTL_S, fold
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
_ENDS = ("item.completed", "item.abandoned")


def _entry(d: dict[str, Any], agent: str) -> dict[str, Any]:
    at = float(d.get("at", 0.0))
    return {
        "holder": d.get("holder", agent),
        "globs": list(d.get("globs", [])),
        "renewed_at": at,
        "ttl_s": int(d.get("ttl_s", DEFAULT_LEASE_TTL_S)),
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
    elif kind in ("lease.released", "lease.expired"):
        cur = leases.get(subject)
        if cur is not None and d.get("holder", cur["holder"]) == cur["holder"]:
            leases.pop(subject)
    elif kind in _ENDS or kind.endswith(".removed"):
        leases.pop(subject, None)  # the item is over: whoever ended it, it holds nothing


def from_log(log: Any, cfg: Config, now: float) -> dict[str, dict[str, Any]]:
    """The live leases of unfinished items, by one fold of ``log`` (the rebuild)."""
    st = fold(log.read_all(), strict=False)
    out: dict[str, dict[str, Any]] = {}
    for item_id, lz in st.active_leases(now, cfg.lease.grace_s).items():
        if item_id not in st.items or st.items[item_id].terminal:
            continue
        out[item_id] = {
            "holder": lz.holder,
            "globs": list(lz.globs),
            "renewed_at": lz.renewed_at,
            "ttl_s": lz.ttl_s,
            "worktree": lz.worktree,
        }
    return out


def _sizes(log: Any) -> dict[str, int] | None:
    """Size of every event shard now: the log's position, cheap (one stat per shard). None when
    a shard cannot be statted: the position is then unknown, never "unchanged"."""
    out: dict[str, int] = {}
    try:
        for sh in log.shards():
            out[sh.name] = sh.stat().st_size
    except OSError:
        return None
    return out


def _doc(
    cur: Any, leases: dict[str, dict[str, Any]], cfg: Config, now: float, log: Any, stamp: list[str]
) -> dict[str, Any]:
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
        "marks": _sizes(log) or {},
        "config_stamp": stamp,
    }


def _continues(
    cur: Any, log: Any, cfg: Config, shard: tuple[str, int, int], now: float, stamp: list[str]
) -> bool:
    """Is ``cur`` exactly the picture of the log up to the event being applied?

    Only then is projecting that one event enough. Otherwise an earlier update failed or never
    ran (a crash, another clone's shard, the knob switched off for a while), the config moved,
    or the file aged out: it is rebuilt from the log instead of being refreshed as if current.
    """
    name0, before, after = shard
    if not (isinstance(cur, dict) and isinstance(cur.get("leases"), dict)):
        return False
    try:
        for v in cur["leases"].values():
            H._lease(v)
    except (TypeError, ValueError, KeyError):
        return False
    marks, limit = cur.get("marks"), cfg.locks.snapshot_max_age_s
    try:
        if limit > 0 and now - float(cur["written_at"]) > limit:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    if not isinstance(marks, dict) or cur.get("config_stamp") != stamp:
        return False
    sizes = _sizes(log)
    if sizes is None or sizes.get(name0) != after:
        return False  # the shard is not exactly where this event left it: others wrote too
    return all(
        (marks.get(name, 0) == before) if name == name0 else (size == marks.get(name, 0))
        for name, size in sizes.items()
    )


def _agents(leases: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Holder -> the items it holds: the caller identity map a hook asks 'who am I'."""
    out: dict[str, list[str]] = {}
    for item_id, lz in sorted(leases.items()):
        out.setdefault(str(lz["holder"]), []).append(item_id)
    return out


def apply(
    log: Any,
    kind: str,
    subject: str,
    data: dict[str, Any],
    agent: str,
    before: int | None = None,
    after: int | None = None,
) -> bool:
    """Bring the snapshot up to date with one event. True when it was written.

    ``before`` is the size of the event's shard before the append (the log hook passes it);
    without it the snapshot is trusted only if it is otherwise current. A write that fails
    removes the file, so the hook reads "missing" (fail open, said) rather than an old picture.
    """
    store = LocalStore(Path(log.root) / ".ddflow" / "local")
    try:
        # The stamp is taken before AND after the load and must agree with itself and be readable:
        # the globs below are then of exactly that config (a change in between is no snapshot).
        stamp = H.config_stamp(log.root)
        cfg = Config.load(log.root)
        if stamp != H.config_stamp(log.root) or H.UNREADABLE_CONFIG in stamp:
            _discard(store)
            return False
        shard = log.shard.name
        if not cfg.locks.snapshot:
            _discard(store)  # off: a file left from before would be an old picture
            return False
    except Exception:
        _discard(store)
        return False

    def step(cur: Any) -> dict[str, Any]:
        now = H.CLOCK.wall()
        if (
            before is not None
            and after is not None
            and _continues(cur, log, cfg, (shard, before, after), now, stamp)
        ):
            leases = {k: dict(v) for k, v in cur["leases"].items()}
            project(leases, kind, subject, data, agent)
        else:
            leases = from_log(log, cfg, now)  # includes this event: it is already appended
        return _doc(cur, leases, cfg, now, log, stamp)

    for attempt in (0, 1):
        try:
            store.update(H.NAME, step, schema=H.DOC_SCHEMA, derived=True)
            return True
        except StoreUnreadable:
            if attempt:
                break
            store.remove(H.NAME)  # torn: rebuilt on the next pass
        except Exception:
            break
    _discard(store)
    return False


def _discard(store: LocalStore) -> None:
    """Make the file unusable: removed, or when it cannot be removed, overwritten with a document
    whose config stamp matches no config (stale at once, whatever the age limit), so a hook never trusts the picture it holds."""
    try:
        store.remove(H.NAME)
    except Exception:
        with contextlib.suppress(Exception):
            store.write(
                H.NAME,
                {"written_at": 0, "leases": {}, "config_stamp": ["discarded"] * len(H.CONFIG_FILES)},
                schema=H.DOC_SCHEMA,
                derived=True,
            )


def appended(log: Any, event: Any, before: int, after: int) -> None:
    """The log's append hook: refresh the snapshot after a lease-relevant event."""
    if event.kind in WATCHED:
        with contextlib.suppress(Exception):
            apply(log, event.kind, event.subject, event.data, event.agent, before, after)


if appended not in APPEND_HOOKS:
    APPEND_HOOKS.append(appended)
