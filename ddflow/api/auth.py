"""`pair` and `tokens ...` (P-remote.auth): the typed surface over `services.auth`. CLI only for
now, like `grant` and `approvals`: a minted secret must not pass through an agent's context."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import auth as AU
from ._base import _load

bind_env = AU.bind_env  #: the CLI's `Ctx` asks here instead of importing the service


def _guarded(kind: str, call) -> O.Outcome:
    """``call``'s Outcome, its refusal or its failure: one reading of the two auth errors."""
    try:
        return call()
    except AU.AuthRefused as exc:
        return O.refused(kind, str(exc))
    except AU.AuthInvalid as exc:
        return O.failed(kind, str(exc))


def _pairing(kind: str, got: AU.Pairing) -> O.Outcome:
    return O.ok(
        kind, code=got.code, name=got.name, role=got.role, expires_at=got.expires_at,
        text=f"one-time code for {got.name} (role {got.role}): {got.code}\n"
        f"valid until {got.expires_at}; the client runs `ddflow tokens redeem` and enters it. "
        "It is shown once.",
    )  # fmt: skip


def pair(repo: Path, args: dict, agent: str = "") -> O.Outcome:
    """A PERSON opens a pairing; the code is in the reply and nowhere else."""
    log, cfg, _st = _load(repo, agent)
    return _guarded("pair", lambda: _pairing("pair", AU.start_pairing(
        log, cfg, name=args["name"], role=args["role"], ttl_h=args.get("ttl_h"),
        requested_agent=agent,
    )))  # fmt: skip


def rotate(repo: Path, args: dict, agent: str = "") -> O.Outcome:
    """A PERSON opens the pairing that replaces a token once redeemed."""
    log, cfg, _st = _load(repo, agent)
    return _guarded("tokens.rotate", lambda: _pairing("tokens.rotate", AU.rotate(
        log, cfg, name=args["name"], ttl_h=args.get("ttl_h"), requested_agent=agent,
    )))  # fmt: skip


def redeem(repo: Path, code: str, agent: str = "") -> O.Outcome:
    """Exchange a one-time code for the token: the reply is the only place it is ever shown."""
    log, cfg, _st = _load(repo, agent)

    def run() -> O.Outcome:
        got = AU.redeem(log, cfg, code)
        return O.ok(
            "tokens.redeem", token=got.token, id=got.id, name=got.name, role=got.role,
            expires_at=got.expires_at,
            text=f"{got.token}\nthe token of {got.name} (role {got.role}), valid until "
            f"{got.expires_at}. It is shown once: present it as {AU.TOKEN_ENV}.",
        )  # fmt: skip

    return _guarded("tokens.redeem", run)


def revoke(repo: Path, args: dict, agent: str = "") -> O.Outcome:
    """A PERSON ends a token."""
    log, cfg, _st = _load(repo, agent)

    def run() -> O.Outcome:
        what = AU.revoke(
            log, cfg, args["name"], reason=args.get("reason") or "", requested_agent=agent
        )
        return O.ok("tokens.revoke", revoked=what, text=f"revoked {what}")

    return _guarded("tokens.revoke", run)


def tokens_list(repo: Path, args: dict, agent: str = "") -> O.Outcome:
    """Who holds a token, from the log: names, roles and dates, never a secret. Where the log
    and the store disagree (a crash between them), the notes say so under the rows."""
    _log, _cfg, st = _load(repo, agent)
    rows = AU.rows(st, everything=bool(args.get("all")))
    text = "\n".join(
        f"{r['name']}  {r['role']}  {r['state']}  until {r['expires_at']}  ({r['id']}, by {r['by']})"
        for r in rows
    )
    if notes := AU.disagreements(repo, AU.rows(st, everything=True)):
        text = f"{text}\n" if text else text
        text += "\n".join(notes)
    return O.ok("tokens.list", tokens=rows, text=text or "no tokens")
