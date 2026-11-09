"""`approvals approve|deny|revoke` (RA-cli): a PERSON's acts, CLI-only.

No declared command with a tool reaches this module (`surfaces/exemptions.py`,
tests/test_approvals_cli.py). An agent identity is refused before anything is read or written,
and the ask engine refuses it again."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..core.handlers.asks import asks_of
from ..services.approvals import answer as AN
from ..services.approvals import view as V
from ._base import _load


def _not_a_person(kind: str, who: str, what: str) -> O.Outcome:
    return O.refused(
        kind,
        f"{what} is a person's act, and this call is an agent's ({who}). Run "
        f"`ddflow {kind.replace('.', ' ')} ...` from your own terminal (ssh works), or answer in "
        "the chat: the agent puts the request to them with `ddflow approvals ask ID` and "
        "`ddflow approvals attest ID` records their answer.",
    )


def _answer(
    repo: Path, ask_id: str, outcome: str, kind: str, *, digest: str, agent: str
) -> O.Outcome:
    if why := AN.why_not_a_person(agent):  # before anything is read
        return _not_a_person(kind, why, "answering an approval")
    log, cfg, st = _load(repo, agent)
    actor = AN.person(cfg, agent)
    if actor.source not in AN.ANSWER_SOURCES:
        return _not_a_person(kind, actor.source, "answering an approval")
    row = asks_of(st).get(ask_id)
    if row is None or not row["verb"]:
        return O.failed(kind, f"no approval request {ask_id}")
    shown = row["digest"]
    if len(digest) < cfg.approvals.min_digest_chars or not shown.startswith(digest.lower()):
        return O.refused(
            kind,
            f"say which one you read: pass --digest with the first {cfg.approvals.min_digest_chars}+ characters of "
            f"the digest shown by `ddflow approvals show {ask_id}`",
        )
    try:
        AN.answer(log, cfg, ask_id, outcome, actor=actor, digest=shown)
    except AN.AskRefused as exc:
        return O.refused(kind, str(exc))
    return O.ok(kind, id=ask_id, outcome=outcome, text=f"{ask_id} {outcome} by {actor.id}")


def approvals_approve(repo: Path, ask_id: str, *, digest: str = "", agent: str = "") -> O.Outcome:
    return _answer(repo, ask_id, AN.APPROVED, "approvals.approve", digest=digest, agent=agent)


def approvals_deny(repo: Path, ask_id: str, *, digest: str = "", agent: str = "") -> O.Outcome:
    return _answer(repo, ask_id, AN.DENIED, "approvals.deny", digest=digest, agent=agent)


def _record(log, cfg, st, ask_id: str, actor, prefix: str) -> str:
    """Answer ONE request; return "" on success, else the reason.

    A non-empty ``prefix`` is read back first, exactly as a single ``approve ID`` checks it;
    a batched command passes "" and the FULL digest is recorded — the read receipt is never
    dropped, it lands in the audit as ``answered_digest`` the way the single form records it."""
    row = asks_of(st).get(ask_id)
    if row is None or not row["verb"]:
        return f"no approval request {ask_id}"
    shown = row["digest"]
    if prefix and (
        len(prefix) < cfg.approvals.min_digest_chars or not shown.startswith(prefix.lower())
    ):
        return (
            f"say which one you read: pass --digest with the first {cfg.approvals.min_digest_chars}+ characters of "
            f"the digest shown by `ddflow approvals show {ask_id}`"
        )
    try:
        AN.answer(log, cfg, ask_id, AN.APPROVED, actor=actor, digest=shown)
    except AN.AskRefused as exc:
        return str(exc)
    return ""


def _split_ids(ids: list[str], digests: list[str]) -> list[tuple[str, str]] | None:
    """`(ask_id, prefix)` per id, one spelling: bare ids with `--digest` (or none), or every id
    a non-empty `ID:DIGEST`. None on a mixed or malformed spelling (`ID:` with no digest, or
    `:DIGEST` with no id)."""
    digests = list(digests)
    pairs = [str(raw).partition(":") for raw in ids]
    has_inline = any(sep == ":" for _, sep, _ in pairs)
    all_pairs = all(ask_id and sep == ":" and prefix for ask_id, sep, prefix in pairs)
    if has_inline and (digests or not all_pairs):
        return None
    target = []
    for i, (ask_id, _, prefix) in enumerate(pairs):
        target.append((ask_id, prefix or (digests[i] if i < len(digests) else "")))
    return target


def approvals_approve_bulk(
    repo: Path,
    ids: list[str],
    *,
    digests: list[str] = (),
    all_pending: bool = False,
    agent: str = "",
) -> O.Outcome:
    """A PERSON approves several requests in one act: `--all`, several ids, `ID:DIGEST`
    pairs, or repeated `--digest`. Each answer records that request's full digest (the read
    receipt a single `approve ID` records); each request is approved at most once, and an
    expired or already-answered request is refused by the engine, not silently skipped."""
    if why := AN.why_not_a_person(agent):  # before anything is read
        return _not_a_person("approvals.approve", why, "answering an approval")
    log, cfg, st = _load(repo, agent)
    actor = AN.person(cfg, agent)
    if actor.source not in AN.ANSWER_SOURCES:
        return _not_a_person("approvals.approve", actor.source, "answering an approval")

    if all_pending:
        target = [
            (r["id"], "")
            for r in V.waiting_for_a_person(st)
            if r["type"] != "question"  # a question is answered in chat, never approved
        ]
    else:
        target = _split_ids(ids, digests)
        if target is None:
            return O.refused(
                "approvals.approve",
                "spell digests one way: ID:DIGEST pairs, or ids with --digest per id -- not both",
            )

    approved: list[str] = []
    refused: list[tuple[str, str]] = []
    for ask_id, prefix in target:
        reason = _record(log, cfg, st, ask_id, actor, prefix)
        if reason:
            refused.append((ask_id, reason))
        else:
            approved.append(ask_id)

    lines = [f"{ask_id} approved by {actor.id}" for ask_id in approved]
    if not approved and not refused:
        return O.ok(
            "approvals.approve", id=[], outcome=AN.APPROVED, text="no approval requests pending"
        )
    if refused:
        why = "; ".join(f"{ask_id}: {reason}" for ask_id, reason in refused)
        return O.refused(
            "approvals.approve",
            f"{len(refused)} not approved — {why}",
            id=approved,
            refused=[{"id": ask_id, "reason": reason} for ask_id, reason in refused],
            outcome=AN.APPROVED,
            text="\n".join(lines),
        )
    return O.ok("approvals.approve", id=approved, outcome=AN.APPROVED, text="\n".join(lines))


def approvals_approve_all(repo: Path, *, agent: str = "") -> O.Outcome:
    """A PERSON approves every currently-pending request, each with its digest recorded."""
    return approvals_approve_bulk(repo, [], all_pending=True, agent=agent)


def approvals_revoke(repo: Path, ask_id: str, *, agent: str = "") -> O.Outcome:
    if why := AN.why_not_a_person(agent):  # before anything is read
        return _not_a_person("approvals.revoke", why, "revoking an approval")
    log, cfg, _st = _load(repo, agent)
    actor = AN.person(cfg, agent)
    if actor.source not in AN.ANSWER_SOURCES:
        return _not_a_person("approvals.revoke", actor.source, "revoking an approval")
    try:
        AN.revoke(log, ask_id, actor=actor)
    except AN.AskRefused as exc:
        return O.refused("approvals.revoke", str(exc))
    return O.ok("approvals.revoke", id=ask_id, text=f"{ask_id} revoked by {actor.id}")


def digest_chars(repo: Path, agent: str = "") -> int:
    """How many leading digest characters the person types back (`[approvals].min_digest_chars`)."""
    if is_an_agents_call(agent):  # no read for an agent; the verbs will refuse it
        return 0
    return _load(repo, agent)[1].approvals.min_digest_chars


def is_an_agents_call(agent: str) -> bool:
    """Is this call an agent's (so the answer verbs will refuse it)? No state is read."""
    return bool(AN.why_not_a_person(agent))
