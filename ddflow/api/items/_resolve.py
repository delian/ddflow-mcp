"""Settling a contested item (`resolve`): keep one definition and/or one claim."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ddflow.api import items as _items

from ...config import csv_list
from ...core import outcome as O
from ...core.records import ADD_RELATIONS, record_pools
from ...services import items as IT
from ...services import leases as L


def _contestants(records: list[dict[str, Any]], keep: str, who: str) -> list[dict[str, Any]]:
    """The records ``keep`` names: by event id, by agent or holder, or by an event-id
    prefix long enough not to be a guess."""
    exact = [r for r in records if keep in (r["event"], r[who])]
    if exact or len(keep) < _MIN_EVENT_PREFIX:
        return exact
    return [r for r in records if r["event"].startswith(keep)]


#: The shortest event-id prefix `resolve --keep` accepts. `e` plus five hex digits.
_MIN_EVENT_PREFIX = 6


def _options(it) -> str:
    rows = [f"{d['event']} ({d['agent']}: {d['title']!r})" for d in it.contested]
    rows += [
        f"{h['holder']} ({'current holder, ' if it.lease and h['event'] == it.lease.event else ''}"
        f"claim {h['event']})"
        for h in it.lease_candidates()
    ]
    return "; ".join(rows)


def _kept_contestants(it, item: str, keep: str) -> tuple[list, list, O.Outcome | None]:
    """(definitions, claims, refusal): the contestants ``keep`` names, or why it names no
    single one."""
    defs = _contestants(it.contested, keep, "agent")
    claims = _contestants(it.lease_candidates(), keep, "holder")
    if not defs and not claims:
        return (
            [],
            [],
            O.failed(
                "item.resolved",
                f"--keep {keep!r} names none of {item}'s contestants: {_options(it)}",
                id=item,
            ),
        )
    if len(defs) > 1 or len(claims) > 1:
        return (
            [],
            [],
            O.failed(
                "item.resolved",
                f"--keep {keep!r} names more than one contestant; give an event id: {_options(it)}",
                id=item,
            ),
        )
    if defs and claims:
        # One token naming a definition AND a claim -- an agent that both filed and
        # claimed it, or a prefix of both ids -- would settle two separate questions
        # at once. Only a full event id says which one was meant.
        if keep == defs[0]["event"]:
            claims = []
        elif keep == claims[0]["event"]:
            defs = []
        else:
            return (
                [],
                [],
                O.refused(
                    "item.resolved",
                    f"--keep {keep!r} names both a definition ({defs[0]['event']}) and a "
                    f"claim ({claims[0]['event']}) of {item}. Settle them one at a time: "
                    f"`--keep {defs[0]['event']}` keeps that definition, "
                    f"`--keep {claims[0]['event']}` keeps that claim.",
                    id=item,
                ),
            )
    return defs, claims, None


def _refile_problem(st, item: str, new_ids: list[str], lost: list) -> O.Outcome | None:
    """The refusal when ``--refile-as`` does not fit the losing definitions, else None."""
    if new_ids and len(new_ids) != len(lost):
        return O.failed(
            "item.resolved",
            f"--refile-as gives {len(new_ids)} id(s) for {len(lost)} losing "
            f"definition(s) of {item}; give one per definition not kept.",
            id=item,
        )
    for nid in new_ids:
        bad = IT.bad_id(nid) or IT.taken(st, nid, readd=False)
        if bad:
            return O.failed("item.resolved", bad, id=item)
    return None


def _resolution(cfg, it, keep: str, defs: list, claims: list) -> dict[str, Any]:
    """The payload of the `item.resolved` event: what was kept."""
    data: dict[str, Any] = {"kind": it.kind, "keep": keep, "at": time.time()}
    if defs:
        data["definition"] = defs[0]
    if claims:
        data["claim"] = claims[0]
        # The TTL a kept claim that had lapsed runs its fresh window on: a recorded
        # expiry zeroed the claim's own, and a window of 0 s is dead on arrival.
        data["ttl_s"] = cfg.lease.ttl_s
    return data


def _apply_resolution(
    log, item: str, it, data: dict, losers: list, claims: list, lost: list, new_ids: list[str]
) -> None:
    """Write the releases, the `item.resolved` event and the refiled losers."""
    # Releases FIRST: folded before the resolution, each withdraws a losing claim,
    # and the resolution then re-applies the kept one whichever was displayed.
    for h in losers:
        L.release_claim(
            log,
            item,
            holder=h["holder"],
            event=h["event"],
            note=f"lost the contest for {item}: {claims[0]['holder']} keeps it",
            lost_to=claims[0]["holder"],
        )
    log.append("item.resolved", item, data)
    for nid, d in zip(new_ids, lost if new_ids else [], strict=True):
        log.append(f"{it.family}.added", nid, d["data"])


def _no_such_item(item: str, it) -> O.Outcome:
    gone = " (it was removed from the queue)" if it is not None else ""
    return O.failed("item.resolved", f"no such item {item!r}{gone}", id=item)


def _resolve_record(log, st, item: str, keep: str, refile_as: str) -> O.Outcome | None:
    """Settle a contested decision, lesson or research id (B230e852e10), or None when
    ``item`` is none of those. The kept definition is re-recorded WHOLE, carrying
    ``resolves`` (the rivals' events) and ``kept``: the fold then replaces the contest
    with it, merging nothing from the others. An older ddflow folds that as an ordinary
    re-record, so no new event kind is needed."""
    if item in st.items:
        return None
    for kind, pool in record_pools(st):
        rec = pool.get(item)
        if rec is None:
            continue
        if not rec.contested:
            return O.refused(
                "item.resolved",
                f"{kind} {item} is not contested: there is nothing to resolve.",
                id=item,
            )
        if refile_as:
            return O.refused(
                "item.resolved",
                f"--refile-as is for items; the losing definition of {kind} {item} stays in "
                f"the log and `show` lists it. Re-add it under a new id by hand.",
                id=item,
            )
        opts = "; ".join(f"{d['event']} ({d['agent']}: {d['title']!r})" for d in rec.contested)
        kept = _contestants(rec.contested, keep, "agent")
        if len(kept) != 1:
            why = "names none of" if not kept else "names more than one of"
            return O.failed(
                "item.resolved",
                f"--keep {keep!r} {why} {item}'s definitions; give an event id: {opts}",
                id=item,
            )
        won = kept[0]
        carry = {k: v for k, v in won["data"].items() if k not in (*ADD_RELATIONS, "dedupe")}
        carry["resolves"] = [d["event"] for d in rec.contested]
        carry["kept"] = won["event"]
        log.append(f"{kind}.recorded", item, carry)
        lost = [d for d in rec.contested if d["event"] != won["event"]]
        return O.ok(
            "item.resolved",
            id=item,
            item_kind=kind,
            kept_definition={k: v for k, v in won.items() if k != "data"},
            kept_holder="",
            lost=[{k: d[k] for k in ("event", "agent", "title")} | {"body": ""} for d in lost],
            refiled=[],
            released=[],
        )
    return None


def resolve(repo: Path, item: str, *, keep: str, refile_as: str = "", agent: str = "") -> O.Outcome:
    """Settle a contested item: keep one definition and/or one claim, recorded as an event.

    B191. A merge can bring in a rival `<kind>.added` or a rival claim, and the fold
    records the contest instead of letting the later lamport silently win. This is the
    way out, and it is an EVENT (`item.resolved`, carrying what was kept) so every clone
    folds to the same answer. A losing definition is not dropped silently either:
    ``refile_as`` re-adds it under new ids (one per loser, in the order `show` lists
    them), and without it the result says how to. A losing CLAIM is released in the same
    transaction. An item that is not contested is refused.

    For a lease contest, ``keep`` may name ANY contestant, not only the one the fold
    displays: every claim that overlapped the kept one gets a `lease.released`, and so
    does the current holder if that is another claim; the resolution then reinstates the
    kept claim with its TTL running from now. A contestant that met neither lost nothing
    to the kept claim and is not released (bug Ba73ee6ea72). Keeping the current holder
    therefore records the claims it met as released; keeping a displaced one hands the
    item back to it. Either way one call settles the whole contest.

    ``keep`` may also name the current holder when it is not a contestant -- it took the
    item over after every contestant had ended -- and then every contestant is released
    and the item stays where it is (B-resolve-cannot-keep-holder).
    """

    log, cfg, _st = _items._load(repo, agent)
    with log.transaction():
        st = L._fold(log)
        it = st.items.get(item)
        if it is None or it.removed:
            settled = _resolve_record(log, st, item, keep, refile_as)
            return _no_such_item(item, it) if settled is None else settled
        if not it.contest_summary():
            return O.refused(
                "item.resolved",
                f"{item} is not contested: there is nothing to resolve. `ddflow update` "
                f"changes an item; `ddflow release` gives up a claim.",
                id=item,
            )
        defs, claims, refusal = _kept_contestants(it, item, keep)
        if refusal is not None:
            return refusal
        lost = [d for d in it.contested if defs and d["event"] != defs[0]["event"]]
        new_ids = csv_list(refile_as)
        if (refusal := _refile_problem(st, item, new_ids, lost)) is not None:
            return refusal
        losers = it.lease_losers(claims[0]) if claims else []
        data = _resolution(cfg, it, keep, defs, claims)
        _apply_resolution(log, item, it, data, losers, claims, lost, new_ids)
    # Outside the lock: each released claim's remote ref goes too, and the kept holder
    # takes the ref a displaced contestant did not hold (Bd45d1ad60e).
    L.settle_remote(
        log,
        cfg,
        item,
        dropped=[h["holder"] for h in losers],
        kept=claims[0]["holder"] if claims else "",
    )
    return O.ok(
        "item.resolved",
        id=item,
        item_kind=it.kind,
        kept_definition={k: v for k, v in defs[0].items() if k != "data"} if defs else None,
        kept_holder=claims[0]["holder"] if claims else "",
        lost=[{k: d[k] for k in ("event", "agent", "title", "body")} for d in lost],
        refiled=new_ids,
        released=[h["holder"] for h in losers],
    )
