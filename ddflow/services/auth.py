"""Paired bearer tokens (P-remote.auth, D-remote): the credential `services/actors` trusts.

A person at the host runs `ddflow pair NAME --role R` and reads out a one-time code; the client
redeems it (`ddflow tokens redeem`, later `POST /v1/pair`) for a token, shown once. The token's
NAME is the caller's identity and its ROLE is what `[agent].roles` and `[agent].capability_verbs`
allow it (`authorize`). What is kept, and where:

* the token and the code exist in clear only in the one reply that shows them;
* the user-level store (`store_path`, 0600 in a 0700 directory, one file per project, never in
  the project) keeps their hashes: enough to verify, not to present. Revoking deletes the row,
  so a revoked token has nothing left to match;
* the log keeps `token.issued` and `token.revoked` with who, when, role and expiry and no secret
  and no hash, so `tokens list` is derived from it and the secret stays out of a committed file.

A verified token is bound in `services/identity`, where `agent_marker`, `is_agent`, `resolve` and
`resolve_actor` read it: every operator-only verb asks those, so one binding is one role check
for all of them. The CLI binds what `DDFLOW_TOKEN` presents (`bind_env`), a server binds per
request (`identity.with_credential`).

What this does NOT do by default: a caller with no token is still judged by its markers alone,
so a shell that unsets them passes as the operator, as before tokens existed. Only a presented
token is held to a role, unless `[agent].require_operator_token` is on (D-operator-token-default:
off, shell access to the machine is the trust boundary).

And with it on: a process of the same OS user can write the store and the config, so
locally this holds against accidents and prompt injection, as `identity.agent_marker` says of
the markers. It is the server, on a host the client cannot reach, that makes a role a boundary.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import re
import secrets
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from ..adapters.clock import DEFAULT as CLOCK
from ..adapters.env import DEFAULT as ENV
from ..config import Config
from ..core import clock
from ..core.digest import content_digest
from ..core.handlers.tokens import tokens_of
from ..infra import fsio
from ..infra import paths as P
from . import identity as ID
from .actors import Actor, capabilities_of, resolve_actor
from .approval import os_user, token_hash
from .ask.engine import _mint_code

TOKEN_ENV = ID.TOKEN_ENV
#: The capability that stands for "the operator": `*` holds it too.
CAP_OPERATOR = "operator"
ACTIVE, EXPIRED, REVOKED = "active", "expired", "revoked"
ISSUED_KIND, REVOKED_KIND = "token.issued", "token.revoked"

#: Protocol literals. `[session].redact_patterns` carries the same shape.
_TOKEN = re.compile(r"ddft_([0-9a-f]{12})_[0-9a-f]{64}")
_CODE = re.compile(r"([0-9a-f]{6})-([0-9A-Z]+)")
_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
_TOKEN_ID = re.compile(r"[0-9a-f]{12}")
STORE_VERSION = 1
_PRIVATE_FILE, _PRIVATE_DIR = 0o600, 0o700
_OTHERS_WRITE = stat.S_IWGRP | stat.S_IWOTH
_NO_TOKEN = (("mcp",), ("tokens", "redeem"))

#: One answer for every way a token or code fails: which it was is no business of the caller's.
_BAD_TOKEN = (
    "token refused: it is unknown, malformed, expired or revoked. "
    "Ask the operator to pair this client again (`ddflow pair`)."
)
_BAD_CODE = (
    "pairing code refused: it is unknown, spent, expired or wrong. "
    "Ask the operator for a new one (`ddflow pair`)."
)


class AuthRefused(ValueError):
    """The credential or the act is refused; the message says why and what to do (exit 3)."""

    exit_code = 3


class AuthInvalid(ValueError):
    """Arguments that are wrong (a bad name, an unknown role, a ttl out of range): exit 1."""


@dataclass(frozen=True)
class Pairing:
    """A started pairing: ``code`` is shown to the person once and never kept."""

    code: str
    name: str
    role: str
    expires_at: str


@dataclass(frozen=True)
class Minted:
    """A redeemed pairing: ``token`` is shown to the client once and never kept."""

    token: str
    id: str
    name: str
    role: str
    expires_at: str


# -- the store -------------------------------------------------------------------------------


def _roots(repo: Path | str) -> tuple[Path, ...]:
    """The tree and the checkout it hangs off: one project, whichever worktree asks."""
    tree = Path(repo).resolve()
    common = P.common_dir(tree, ask_git=False)
    return (tree,) if common is None else (tree, common.resolve().parent)


def project_key(repo: Path | str) -> str:
    """Which project a token is for: a token paired for one project proves nothing in another."""
    return content_digest(str(_roots(repo)[-1]), "sha256")[:16]


def store_path(repo: Path | str) -> Path:
    """`$XDG_CONFIG_HOME/ddflow/auth/<project>.json`, else under `~/.config`. From the user's
    environment only: `[agent]` has no knob for it, so a project cannot name its own hashes."""
    base = ENV.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ddflow" / "auth" / f"{project_key(repo)}.json"


def _store(repo: Path | str) -> Path:
    path = store_path(repo)
    resolved = path.parent.resolve()
    if any(r == resolved or r in resolved.parents for r in _roots(repo)):
        raise AuthRefused(
            f"refusing: the user-level directory is inside the project ({path.parent}), and "
            "token hashes never live in the project. Point XDG_CONFIG_HOME somewhere else."
        )
    return path


def _empty() -> dict[str, Any]:
    return {"format": STORE_VERSION, "tokens": {}, "pairings": {}}


def _read(path: Path) -> dict[str, Any]:
    """The store, or an empty one when there is none. Fails closed: a store that others could
    have written, or that does not parse, verifies nothing."""
    try:
        info = os.lstat(path)
        parent = os.stat(path.parent)
    except FileNotFoundError:
        return _empty()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or (info.st_mode | parent.st_mode) & _OTHERS_WRITE
    ):
        raise AuthRefused(
            f"refusing: the token store {path} has unsafe permissions (it must be a regular "
            "file of yours that nobody else can write). Fix it, or pair again from a new store."
        )
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise AuthRefused(f"refusing: the token store {path} is unreadable ({exc}).") from exc
    if not isinstance(data, dict) or data.get("format") != STORE_VERSION:
        raise AuthRefused(f"refusing: the token store {path} is not one this ddflow reads.")
    for table in ("tokens", "pairings"):
        if table in data and not isinstance(data.get(table), dict):
            raise AuthRefused(
                f"refusing: the token store {path} has {table!r} as "
                f"{type(data.get(table)).__name__}, not a table; it refuses instead of "
                "crashing on the wrong shape. Fix the store, or pair again from a new store."
            )
    return {**_empty(), **data}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR)
    os.chmod(path.parent, _PRIVATE_DIR)
    fsio.atomic_write(path, json.dumps(data, indent=1, sort_keys=True), mode=_PRIVATE_FILE)


def _settle(path: Path, data: dict[str, Any], read: str) -> None:
    if json.dumps(data, sort_keys=True) != read:
        _write(path, data)


@contextlib.contextmanager
def _changing(repo: Path | str, cfg: Config, now: float) -> Iterator[dict[str, Any]]:
    """The store for one read-modify-write, under its own lock (two worktrees of a project have
    two logs and one store) and with what has lapsed dropped. Written back when it changed: on a
    clean exit and on `AuthRefused`, because a burnt guess must stay burnt though the call is
    refused. Any other exception writes nothing."""
    path = _store(repo)
    path.parent.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR)
    with fsio.file_lock(fsio.lock_path_for(path), cfg.lease.acquire_timeout_s):
        data = _read(path)
        read = json.dumps(data, sort_keys=True)
        for table in ("tokens", "pairings"):
            data[table] = {k: r for k, r in data[table].items() if r["expires"] > now}
        try:
            yield data
        except AuthRefused:
            _settle(path, data, read)
            raise
        _settle(path, data, read)


def _operator(cfg: Config, role: str) -> bool:
    held = capabilities_of(cfg, role)
    return "*" in held or CAP_OPERATOR in held


def _live_operator(cfg: Config, data: dict[str, Any]) -> bool:
    return any(_operator(cfg, row["role"]) for row in data["tokens"].values())


def _person(cfg: Config, data: dict[str, Any], requested_agent: str, *, first: bool) -> None:
    """Refuse anything but the operator. ``first``: this mints an operator token, and a
    marker-free terminal may mint the first one under `[agent].require_operator_token`, or
    nobody ever could, or recover after the last one expired (D-operator-token-default). Any
    other role waits for a live operator token."""
    why = ID.agent_marker(requested_agent)
    if not why:
        return
    if first and ID.lacks_only_proof(requested_agent) and not _live_operator(cfg, data):
        return
    raise AuthRefused(
        f"refusing: minting or revoking a credential is a person's act at the host, and this "
        f"call is not one ({why}). A token must never pass through an agent's context, so "
        "the operator runs this from their own terminal, or presents an operator token."
    )


# -- pairing ---------------------------------------------------------------------------------


def _ttl_h(cfg: Config, ttl_h: int | None) -> int:
    ttl, most = cfg.agent.token_ttl_h if ttl_h is None else ttl_h, cfg.agent.token_max_ttl_h
    if not 1 <= ttl <= most:
        raise AuthInvalid(f"--ttl-h must be 1..{most} ([agent].token_max_ttl_h); got {ttl}")
    return ttl


def _who() -> str:
    """Who is acting for the audit: the proved name, else the OS user."""
    proof = ID.credential()
    return os_user() if proof is None else proof.name


def _open_pairing(
    cfg: Config, data: dict[str, Any], name: str, role: str, ttl: int, rotates: str, now: float
) -> Pairing:
    pair_id, salt = secrets.token_hex(3), secrets.token_hex(8)
    secret = _mint_code(cfg.agent.pair_code_length)
    expires = now + cfg.agent.pair_ttl_s
    data["pairings"][pair_id] = {
        "name": name, "role": role, "ttl_h": ttl, "rotates": rotates, "salt": salt,
        "hash": token_hash(salt + secret), "expires": expires, "attempts": 0,
        "by": _who(),
    }  # fmt: skip
    return Pairing(f"{pair_id}-{secret}", name, role, clock.iso_at(expires, timespec="seconds"))


def _taken(data: dict[str, Any], name: str) -> bool:
    return any(r["name"] == name for t in ("tokens", "pairings") for r in data[t].values())


def start_pairing(
    log, cfg: Config, *, name: str, role: str, ttl_h: int | None = None, requested_agent: str = "",
    now: float | None = None,
) -> Pairing:  # fmt: skip
    """A PERSON opens a pairing for client ``name`` with ``role``; the code is shown once."""
    moment = CLOCK.wall() if now is None else now
    with _changing(log.root, cfg, moment) as data:
        _person(cfg, data, requested_agent, first=_operator(cfg, role))
        if not _NAME.fullmatch(name) or _TOKEN_ID.fullmatch(name):
            raise AuthInvalid(
                f"bad client name {name!r}: letters, digits, . _ - and at most 64, "
                "and not the shape of a token id (`tokens revoke` takes either)"
            )
        if role not in cfg.agent.roles:
            raise AuthInvalid(
                f"no role {role!r}; [agent].roles has {', '.join(sorted(cfg.agent.roles))}"
            )
        ttl = _ttl_h(cfg, ttl_h)
        if _taken(data, name):
            raise AuthRefused(
                f"{name} already has a live token or a waiting pairing: "
                f"`ddflow tokens rotate {name}` replaces it, `ddflow tokens revoke {name}` ends it."
            )
        return _open_pairing(cfg, data, name, role, ttl, "", moment)


def rotate(
    log, cfg: Config, *, name: str, ttl_h: int | None = None, requested_agent: str = "",
    now: float | None = None,
) -> Pairing:  # fmt: skip
    """A PERSON opens a pairing that replaces ``name``'s token: the old one works until the new
    one is redeemed, so a client is never left without a credential by a code it never got."""
    moment = CLOCK.wall() if now is None else now
    with _changing(log.root, cfg, moment) as data:
        _person(cfg, data, requested_agent, first=False)
        ttl = _ttl_h(cfg, ttl_h)
        found = _find(data, name)
        if any(r["name"] == found[1]["name"] for r in data["pairings"].values()):
            raise AuthRefused(f"{name} already has a waiting pairing; redeem or revoke it first.")
        return _open_pairing(cfg, data, found[1]["name"], found[1]["role"], ttl, found[0], moment)


def _find(data: dict[str, Any], who: str) -> tuple[str, dict[str, Any]]:
    """The live token named ``who`` or with that id: `start_pairing` keeps the two apart."""
    for token_id, row in data["tokens"].items():
        if who in (token_id, row["name"]):
            return token_id, row
    raise AuthRefused(f"no live token named {who!r} on this host (`ddflow tokens list`).")


def _spend(cfg: Config, data: dict[str, Any], code: str) -> dict[str, Any]:
    """The pairing ``code`` opens, removed from the store. A wrong secret for a live pairing
    counts against it, and the last allowed attempt burns it."""
    m = _CODE.fullmatch(code.strip())
    if m is None or (row := data["pairings"].get(m.group(1))) is None:
        raise AuthRefused(_BAD_CODE)
    if not hmac.compare_digest(token_hash(row["salt"] + m.group(2)), row["hash"]):
        row["attempts"] += 1
        if row["attempts"] >= cfg.agent.pair_max_attempts:
            del data["pairings"][m.group(1)]
        raise AuthRefused(_BAD_CODE)
    return data["pairings"].pop(m.group(1))


def redeem(log, cfg: Config, code: str, *, now: float | None = None) -> Minted:
    """Exchange a one-time code for a token: the only moment the token exists in clear. The
    code is the proof, so this needs no identity. The store is written first, as in `revoke`:
    a crash after it leaves a token nobody was shown, or an unrecorded rotation; the other order
    could leave an old token that still verifies though the log says it was replaced."""
    moment = CLOCK.wall() if now is None else now
    with _changing(log.root, cfg, moment) as data:
        row = _spend(cfg, data, code)
        token_id, secret = secrets.token_hex(6), secrets.token_hex(32)
        token = f"ddft_{token_id}_{secret}"
        expires = moment + row["ttl_h"] * 3600
        old = data["tokens"].pop(row["rotates"], None) if row["rotates"] else None
        data["tokens"][token_id] = {
            "name": row["name"], "role": row["role"], "hash": token_hash(token), "expires": expires,
        }  # fmt: skip
    expires_at = clock.iso_at(expires, timespec="seconds")
    with log.transaction():
        if old is not None:
            log.append(REVOKED_KIND, row["rotates"], {
                "id": row["rotates"], "name": old["name"], "by": row["by"], "reason": "rotated",
            })  # fmt: skip
        log.append(ISSUED_KIND, token_id, {
            "id": token_id, "name": row["name"], "role": row["role"], "expires_at": expires_at,
            "by": row["by"], "rotated_from": row["rotates"] if old is not None else "",
        })  # fmt: skip
    return Minted(token, token_id, row["name"], row["role"], expires_at)


def revoke(
    log, cfg: Config, who: str, *, reason: str, requested_agent: str = "",
    now: float | None = None,
) -> str:  # fmt: skip
    """A PERSON ends ``who``'s token (name or id) and any pairing waiting for that name. The row
    goes first: the effect must not wait on the record. Returns what ended."""
    moment = CLOCK.wall() if now is None else now
    with _changing(log.root, cfg, moment) as data:
        _person(cfg, data, requested_agent, first=False)
        if not reason.strip():
            raise AuthInvalid("a revocation needs --reason: why this credential ended")
        waiting = [k for k, r in data["pairings"].items() if r["name"] == who]
        for pair_id in waiting:
            del data["pairings"][pair_id]
        try:
            token_id, row = _find(data, who)
        except AuthRefused:
            if waiting:
                return f"pairing for {who}"
            raise
        name = row["name"]
        for pair_id in [k for k, r in data["pairings"].items() if r["rotates"] == token_id]:
            del data["pairings"][pair_id]
        del data["tokens"][token_id]
    log.append(REVOKED_KIND, token_id, {
        "id": token_id, "name": name, "by": _who(), "reason": reason.strip(),
    })  # fmt: skip
    return f"token {token_id} of {name}"


# -- verification ----------------------------------------------------------------------------


def verify(repo: Path | str, cfg: Config, token: str, *, now: float | None = None) -> ID.Credential:
    """What ``token`` proves, or `AuthRefused`. Reads only: no lock and no write per call."""
    m = _TOKEN.fullmatch(token)
    if m is None:
        raise AuthRefused(_BAD_TOKEN)
    row = _read(_store(repo))["tokens"].get(m.group(1))
    moment = CLOCK.wall() if now is None else now
    if row is None or not hmac.compare_digest(token_hash(token), row["hash"]):
        raise AuthRefused(_BAD_TOKEN)
    if row["expires"] <= moment:
        raise AuthRefused(_BAD_TOKEN)
    return ID.Credential(row["name"], row["role"], m.group(1), _operator(cfg, row["role"]))


def authorize(cfg: Config, actor: Actor, verb: str) -> str:
    """ "" when ``actor`` may run ``verb`` (`gate status`), else why not. Deny by default: a verb
    no capability of the role names is the operator's. The one table the CLI and a server ask."""
    if actor.can(CAP_OPERATOR):
        return ""
    table = cfg.agent.capability_verbs
    if any(
        fnmatchcase(verb, pattern)
        for capability in actor.capabilities
        for pattern in table.get(capability, ())
    ):
        return ""
    return (
        f"{actor.id} acts as role '{actor.role}' ({actor.source}), which does not allow "
        f"`{verb}`. Who may run what is [agent].roles with [agent].capability_verbs "
        "(`ddflow config agent.capability_verbs`); widening it is the operator's."
    )


def bind_env(repo: Path | str, cfg: Config, path: tuple[str, ...]) -> ID.Credential | None:
    """Prove this CLI call from `DDFLOW_TOKEN` and hold it to the role's verbs. The variable
    leaves the environment here, so no lane or gate this process starts inherits it. A bad token
    refuses the call: presenting one and falling back to "a person" would fail open.

    Two commands take no token. The MCP server: a stdio server speaks for whichever agent
    connected, and the operator's token in its environment would make every agent the operator.
    `tokens redeem`: the code is the proof, and a client rotating still has the old token set."""
    raw = ENV.take(TOKEN_ENV)
    if path in _NO_TOKEN:
        return None
    if not raw:
        if cfg.agent.require_operator_token:
            ID.require_proof()
        return None
    proof = verify(repo, cfg, raw.strip())
    ID.bind_credential(proof)
    if why := authorize(cfg, resolve_actor(cfg, proof.name), " ".join(path)):
        raise AuthRefused(why)
    return proof


# -- the record ------------------------------------------------------------------------------


def state_of(row: dict[str, Any], now: float | None = None) -> str:
    """A folded token's state: the fold reads no clock, so expiry is answered here."""
    if row["revoked_at"]:
        return REVOKED
    moment = CLOCK.wall() if now is None else now
    return EXPIRED if clock.parse_ts(row["expires_at"]).timestamp() <= moment else ACTIVE


def rows(st, *, everything: bool = False, now: float | None = None) -> list[dict[str, Any]]:
    """The tokens the log knows, oldest first: only the active ones unless ``everything``."""
    out = [{**r, "state": state_of(r, now)} for r in tokens_of(st).values()]
    return [r for r in out if everything or r["state"] == ACTIVE]


def disagreements(repo: Path | str, logged: list[dict[str, Any]]) -> list[str]:
    """Where `tokens list` (the log) and `verify` (the store) part company. `redeem` and
    `revoke` write the store first and the log second, so a crash between the two leaves one
    side ahead and a list derived from the log alone would lie. One note for each, said next
    to the rows; the list itself stays "... is derived from the log", and these are how it is
    reconciled against what actually verifies.

    ``logged`` is every row the log knows (all states), so "has no log record" means none, not
    "none still active". A store `_read` refuses (unreadable, unsafe permissions) is not this:
    `verify` refuses every token then and `_read` already names it loudly; there is no
    ordering divergence to reconcile."""
    try:
        stored = _read(_store(repo))["tokens"]
    except AuthRefused:
        return []
    now = CLOCK.wall()
    by_id = {r["id"]: r for r in logged}
    notes: list[str] = []

    def live(row: Any) -> bool:
        exp = row.get("expires") if isinstance(row, dict) else None
        return isinstance(exp, (int, float)) and exp > now

    for token_id in sorted(set(stored) | set(by_id)):
        s_live = live(stored.get(token_id))
        l_row = by_id.get(token_id)
        l_active = l_row is not None and l_row.get("state") == ACTIVE
        if s_live and l_row is None:
            notes.append(
                f"unrecorded: token {token_id} of {stored[token_id].get('name')} is live in "
                f"the store but has no log record (a crash between the store and the log); "
                f"`tokens revoke {stored[token_id].get('name')}` ends it"
            )
        elif s_live != l_active:
            name = stored[token_id].get("name") if s_live else l_row.get("name")
            tail = (
                "is live in the store but not listed as active; it will still verify"
                if s_live
                else "is listed active but the store has ended or expired it; it will not verify"
            )
            notes.append(f"unrecorded: token {token_id} of {name} {tail}")
    return notes
