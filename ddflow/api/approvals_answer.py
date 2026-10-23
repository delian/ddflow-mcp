"""`approvals approve|deny|revoke` (RA-cli): a PERSON's acts, CLI-only.

No declared command with a tool reaches this module (`surfaces/exemptions.py`,
tests/test_approvals_cli.py). An agent identity is refused before anything is read or written,
and the ask engine refuses it again."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..core.handlers.asks import asks_of
from ..services.approvals import answer as AN
from ._base import _load


def _not_a_person(kind: str, actor: AN.Actor, what: str) -> O.Outcome:
    return O.refused(
        kind,
        f"{what} is a person's act, and this call is an agent's ({actor.source}). Run "
        f"`ddflow {kind.replace('.', ' ')} ...` from your own terminal (ssh works).",
    )


def _answer(
    repo: Path, ask_id: str, outcome: str, kind: str, *, digest: str, agent: str
) -> O.Outcome:
    log, cfg, st = _load(repo, agent)
    actor = AN.person(cfg, agent)
    if actor.source not in AN.ANSWER_SOURCES:
        return _not_a_person(kind, actor, "answering an approval")
    row = asks_of(st).get(ask_id)
    if row is None or not row["verb"]:
        return O.failed(kind, f"no approval request {ask_id}")
    shown = row["digest"]
    if len(digest) < cfg.approvals.min_digest_chars or not shown.startswith(digest.lower()):
        return O.refused(
            kind,
            f"say which one you read: pass --digest with the first {cfg.approvals.min_digest_chars}+ characters of "
            f"the digest shown by `ddflow approvals show {ask_id}` ({shown[:16]}...)",
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


def approvals_revoke(repo: Path, ask_id: str, *, agent: str = "") -> O.Outcome:
    log, cfg, _st = _load(repo, agent)
    actor = AN.person(cfg, agent)
    if actor.source not in AN.ANSWER_SOURCES:
        return _not_a_person("approvals.revoke", actor, "revoking an approval")
    try:
        AN.revoke(log, ask_id, actor=actor)
    except AN.AskRefused as exc:
        return O.refused("approvals.revoke", str(exc))
    return O.ok("approvals.revoke", id=ask_id, text=f"{ask_id} revoked by {actor.id}")
