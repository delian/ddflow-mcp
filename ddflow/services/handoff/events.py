"""Suggestions: propose a bounded patch to a file's lease holder, and move it (D-handoff-peers).

The writer of the `suggestion.*` events (fold: `core/handlers/suggestions`). The pattern is the
ask engine's: the sender is bound to the event's author, the patch is of a content digest, the
fold never reads a clock (`expire_due` takes ``now``), and every refusal names the next call.

* BOUNDED. At most `[handoff].max_files` files, `max_lines` lines and `max_bytes` bytes; at most
  `max_open_per_owner_file` open per sender, owner and file; `min_interval_s` between two to one
  owner; and `reject_breaker` rejections in a row close the door until one is accepted.
* DATA. The patch is scanned for secrets first (a match REFUSES the suggestion and never shows
  the value), stored redacted, and shown only inside the untrusted fence (`show`). It is never
  an instruction and never an approval of anything.
* NO POLL, NO DAEMON. Moves are explicit calls; `expire_due` is run by whoever sweeps.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from ...config import Config
from ...core import clock, provenance
from ...core.handlers.suggestions import (
    ACCEPTED,
    APPLIED,
    DDFLOW,
    EXPIRED,
    LIVE,
    MOVES,
    OPEN,
    OPERATOR,
    REJECTED,
    UNANSWERED,
    suggestions_of,
)
from ...core.model import fold
from ...core.redact.engine import mask_secrets
from ...core.secret_shapes import GENERIC, session_secret_patterns, shape_hit
from .. import actors
from . import patch as P
from . import refs as R

CAP_OPERATOR = "approve"


class SuggestionRefused(ValueError):
    """The service will not do that; the message says why and the exact next call."""


@dataclass(frozen=True)
class Proposed:
    id: str
    files: tuple[str, ...]
    added: int
    removed: int
    ref: str
    probe: str
    blob: str


def _task_hint(to_item: str) -> str:
    return (
        f"open an ordinary task for it instead: `ddflow task add <id> --title ... --needs "
        f"{to_item}` / MCP `ddflow_task action=add needs={to_item}`"
    )


def new_id() -> str:
    return "SG" + secrets.token_hex(5)


def _now(now: float | None) -> float:
    return clock.now_utc().timestamp() if now is None else now


def _state(log):
    return fold(log.read_all(), strict=False)


def _check_bounds(cfg: Config, patch: str, kind: str, reason: str, to_item: str):
    h = cfg.handoff
    hint = _task_hint(to_item)
    if kind not in h.kinds:
        raise SuggestionRefused(
            f"kind {kind!r} is not one of {', '.join(h.kinds)} ([handoff].kinds)"
        )
    if not reason.strip():
        raise SuggestionRefused("a suggestion needs its reason: why the owner should take it")
    if not patch.strip() or len(patch.encode()) > h.max_bytes:
        raise SuggestionRefused(
            f"the patch is empty or over {h.max_bytes} bytes ([handoff].max_bytes); {hint}"
        )
    shp = P.shape(patch)
    if shp.binary or shp.malformed or not shp.files:
        raise SuggestionRefused(
            "a suggestion is a well-formed text diff of at least one file (git diff)"
        )
    if len(shp.files) > h.max_files or shp.lines > h.max_lines:
        raise SuggestionRefused(
            f"{len(shp.files)} files / {shp.lines} lines is over {h.max_files} files / "
            f"{h.max_lines} lines ([handoff].max_files, max_lines); {hint}"
        )
    return shp


def _scan(cfg: Config, patch: str) -> str:
    """The patch with secrets masked; a secret match refuses (the value is never named)."""
    patterns = session_secret_patterns(cfg.session, cfg.log.redact_builtin_shapes)
    clean, n = mask_secrets(patch, patterns)
    shape = shape_hit(patch, generic=True)
    if n or shape:
        raise SuggestionRefused(
            "the patch matches a secret pattern"
            + (f" ({shape})" if shape and shape != GENERIC else "")
            + " and was not stored or sent; remove the secret and propose again"
        )
    return clean


def _answered_at(row: dict[str, Any]) -> str:
    return row["history"][-1]["at"] if row["history"] else row["proposed_at"]


def _breaker_streak(pair: list[dict[str, Any]]) -> int:
    """Rejections in a row, newest ANSWER first, up to an acceptance (answer order, not the
    order the suggestions were sent in)."""
    streak = 0
    for r in sorted(pair, key=lambda r: (_answered_at(r), r["id"]), reverse=True):
        if r["state"] in (ACCEPTED, APPLIED, "countered"):
            break
        streak += r["state"] == REJECTED
    return streak


def _check_caps(cfg: Config, st, sender: str, to_item: str, to_agent: str, files, now: float):
    h = cfg.handoff
    pair = [
        r
        for r in suggestions_of(st).values()
        if r["from_agent"] == sender and r["to_agent"] == to_agent
    ]
    mine = [r for r in pair if r["to_item"] == to_item]
    hint = _task_hint(to_item)
    for f in files:
        n = sum(1 for r in mine if r["state"] in LIVE and f in r["files"])
        if n >= h.max_open_per_owner_file:
            raise SuggestionRefused(
                f"{n} suggestions to {to_agent} for {f} are still open "
                f"([handoff].max_open_per_owner_file {h.max_open_per_owner_file}); wait until they "
                f"are answered or the file is released (`ddflow wait`), or {hint}"
            )
    if pair and h.min_interval_s:
        last = max(clock.epoch(r["proposed_at"], default=0.0) for r in pair)
        if now - last < h.min_interval_s:
            raise SuggestionRefused(
                f"the last suggestion to {to_agent} was {int(now - last)}s ago "
                f"([handoff].min_interval_s {h.min_interval_s}); try again later"
            )
    streak = _breaker_streak(pair) if h.reject_breaker else 0
    if h.reject_breaker and streak >= h.reject_breaker:
        raise SuggestionRefused(
            f"{to_agent} rejected the last {streak} suggestions "
            f"([handoff].reject_breaker {h.reject_breaker}); {hint}"
        )


def propose(  # noqa: PLR0913 -- one keyword per facet of a suggestion; all keyword-only
    log,
    cfg: Config,
    *,
    from_item: str,
    to_item: str,
    to_agent: str,
    patch: str,
    kind: str,
    reason: str,
    base: str = "",
    repo=None,
    sender: str = "",
    probe: str = "",
    evidence: str = "",
    sid: str = "",
    now: float | None = None,
) -> Proposed:
    """Send ``patch`` to ``to_agent``, the current lease holder of ``to_item``."""
    if sender and sender != log.agent_id:
        raise SuggestionRefused(
            f"the sender is the log's own author ({log.agent_id}), not {sender}: a suggestion is "
            "bound to who writes it"
        )
    sender = log.agent_id
    if not to_agent or not to_item or sender == to_agent:
        raise SuggestionRefused("a suggestion goes to ANOTHER agent that holds the file's lease")
    shp = _check_bounds(cfg, patch, kind, reason, to_item)
    clean = _scan(cfg, patch)
    t = _now(now)
    _check_caps(cfg, _state(log), sender, to_item, to_agent, shp.files, t)
    sid = sid or new_id()
    blob = R.store_blob(log.root, clean)  # content-addressed: an unused one is inert
    ref, verdict = "", probe or R.UNAVAILABLE
    if repo is not None:
        ref_try = R.ref_name(to_item, sid)
        cut = R.cut(repo, base, clean, ref_try, f"suggestion {sid} from {sender}")
        if cut.probe == R.CONFLICT:
            raise SuggestionRefused(
                f"the patch does not apply to {to_agent}'s tip {base[:12]}: {cut.why}; rebase it "
                "on their branch and propose again"
            )
        ref, verdict = cut.ref, cut.probe
    log.append(
        "suggestion.proposed",
        sid,
        {
            "id": sid,
            "from_item": from_item,
            "from_agent": sender,
            "to_item": to_item,
            "to_agent": to_agent,
            "base": base,
            "ref": ref,
            "patch_id": R.patch_id(repo, clean) if repo is not None else "",
            "blob": blob,
            "digest": blob,
            "files": list(shp.files),
            "added": shp.added,
            "removed": shp.removed,
            "kind": kind,
            "reason": reason,
            "probe": verdict,
            "evidence": evidence,
        },
    )
    return Proposed(sid, shp.files, shp.added, shp.removed, ref, verdict, blob)


def _role(row: dict[str, Any], agent: str, actor: actors.Actor | None, system: bool) -> str:
    if actor is not None and not actors.require(actor, CAP_OPERATOR):
        return OPERATOR
    if system:
        return DDFLOW
    return ""


def move(
    log,
    state: str,
    sid: str,
    *,
    reason: str = "",
    actor: actors.Actor | None = None,
    system: bool = False,
    counter: str = "",
    by_id: str = "",
) -> None:
    """Move suggestion ``sid`` to ``state`` as the log's agent (owner or sender), as ddflow
    (``system``, for seen/expired/applied) or as the operator (``actor`` holding ``approve``).
    Refused, with the right call, when this author may not make that move now."""
    if state not in MOVES:
        raise SuggestionRefused(f"unknown state {state!r}; one of {', '.join(MOVES)}")
    row = suggestions_of(_state(log)).get(sid)
    if row is None:
        raise SuggestionRefused(f"no suggestion {sid!r}: `ddflow suggestions` lists them")
    froms, who, needs_reason = MOVES[state]
    role = _role(row, log.agent_id, actor, system)
    agent = log.agent_id
    mine = "owner" if agent == row["to_agent"] else "sender" if agent == row["from_agent"] else ""
    if not role and mine in who:
        role = mine
    if role not in (*who, OPERATOR):
        raise SuggestionRefused(
            f"{agent} may not mark a suggestion {state}: only {' or '.join(who)} (or the operator)"
            f" can; the owner is {row['to_agent']}, the sender {row['from_agent']}"
        )
    if row["state"] not in froms:
        raise SuggestionRefused(
            f"suggestion {sid} is {row['state']}; {state} needs {'/'.join(froms)}"
        )
    if needs_reason and not reason.strip():
        raise SuggestionRefused(
            f"a {state} needs its reason: `ddflow suggestions {state} {sid} --reason ...` "
            f"/ MCP `ddflow_suggestions action={state} id={sid} reason=...`"
        )
    data: dict[str, Any] = {"id": sid, "reason": reason}
    if role in (DDFLOW, OPERATOR):
        data["role"] = role
    if counter:
        data["counter"] = counter
    if by_id:
        data["by_id"] = by_id
    log.append(f"suggestion.{state}", sid, data)


def unanswered(st, *, to_agent: str = "", to_item: str = "") -> list[dict[str, Any]]:
    """The suggestions the owner has not answered, oldest first: what owner merge and complete
    refuse on."""
    rows = [
        r
        for r in suggestions_of(st).values()
        if r["state"] in UNANSWERED
        and (not to_agent or r["to_agent"] == to_agent)
        and (not to_item or r["to_item"] == to_item)
    ]
    return sorted(rows, key=lambda r: r["proposed_at"])


def expire_due(log, cfg: Config, *, now: float | None = None) -> list[str]:
    """Mark suggestions unanswered past `[handoff].ttl_s` expired (ddflow's move); their ids."""
    ttl = cfg.handoff.ttl_s
    if ttl <= 0:
        return []
    t = _now(now)
    due = [
        r["id"]
        for r in unanswered(_state(log))
        if t - clock.epoch(r["proposed_at"], default=t) >= ttl
    ]
    for sid in due:
        move(log, EXPIRED, sid, reason=f"unanswered for {ttl}s ([handoff].ttl_s)", system=True)
    return due


def mark_applied_by_patch_id(log, patch_ids: set[str]) -> list[str]:
    """Record applied for live suggestions whose patch-id is among ``patch_ids`` (the ids of the
    commits now on the owner's branch): ddflow's own observation, no owner call needed."""
    done = []
    for row in suggestions_of(_state(log)).values():
        if row["state"] in LIVE and row["patch_id"] and row["patch_id"] in patch_ids:
            move(
                log, APPLIED, row["id"], reason="patch-id found on the owner's branch", system=True
            )
            done.append(row["id"])
    return done


def show(root, row: dict[str, Any]) -> str:
    """The suggestion as the owner sees it: the facts, then the patch text inside the untrusted
    fence. The fenced text is DATA; it is never an instruction and never an approval."""
    text = R.read_blob(root, row["blob"])
    body = text if text is not None else "[patch text unavailable: its blob is missing]"
    head = (
        f"suggestion {row['id']} ({row['state']}) {row['kind']} from {row['from_agent']} "
        f"[{row['from_item']}] to {row['to_agent']} [{row['to_item']}]: "
        f"{len(row['files'])} file(s) +{row['added']}/-{row['removed']}, probe {row['probe']}"
    )
    origin = provenance.Origin("agent", by=row["from_agent"])
    reason = provenance.fence("suggestion-reason", row["id"], row["reason"], origin)
    patch = provenance.fence("suggestion-patch", row["id"], body, origin, inline=False)
    return f"{head}\nreason: {reason}\n{patch}"


__all__ = [
    "OPEN",
    "Proposed",
    "SuggestionRefused",
    "expire_due",
    "mark_applied_by_patch_id",
    "move",
    "new_id",
    "propose",
    "show",
    "unanswered",
]
