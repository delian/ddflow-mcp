"""Build a `HumanChannel` from its settings, with consent once per kind (ADP-notify-ask).

The adapters read no configuration and decide nothing; this module does both, and is the one place
that does. `Settings` holds what a person can tune, with the defaults; the `[notify]` knobs that
fill it are ADP-notify-knobs, kept out of this item so the port touches only its own files
(r-adapters). Chat, webhook and email reach the network, so each needs a person's consent once per
KIND (D-adapters-answers-3): `grant_consent` records it through the approve-by-digest primitive,
which refuses an agent identity, and a channel without it is an `UnavailableChannel` that sends
nothing. Endpoints are secrets (a chat webhook's URL is its credential), so a value from a
committed layer is refused, and tokens are read from the variable a setting names (r-secrets).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...adapters import env as ENV
from ...adapters import http as HTTP
from ...adapters import notify as N
from ...adapters.notify import Answer, Approval, HumanChannel
from ...adapters.proc import Proc
from ...config import Config
from ...core.digest import content_digest
from ...core.model import fold
from ...infra import proc as P
from .. import approval
from ..approvals import harness as H
from ..redact_report import redactor
from . import engine as E

#: Adapter name -> its consent kind. An adapter not named here stays on this machine.
KINDS = {
    "email": "email",
    "n8n": "webhook",
    "openclaw": "webhook",
    "slack": "chat",
    "teams": "chat",
    "webhook": "webhook",
}
#: The settings that hold an address, per adapter: none of them may come from a committed layer.
ADDRESSES = ("email_host", "email_from", "email_to", "email_user")


@dataclass(frozen=True)
class Settings:
    """What a person can tune about the channels. ``committed`` names the fields whose value came
    from a committed layer: an address there makes its adapter unavailable (r-secrets)."""

    channel: str = "terminal"  #: where notifications go
    ask_channel: str = "harness"  #: where questions and approvals go: the chat by default
    endpoints: Mapping[str, str] = field(default_factory=dict)  #: adapter -> its whole hook URL
    token_env: Mapping[str, str] = field(
        default_factory=dict
    )  #: adapter -> variable with its token
    send_timeout_s: float = 10.0  #: one webhook POST, mail exchange or desktop command
    ask_timeout_s: float = 300.0  #: the terminal's wait for one line
    ask_attempts: int = 3  #: unusable answers the terminal takes before it gives up
    email_host: str = ""
    email_port: int = 587
    email_from: str = ""
    email_to: str = ""
    email_user: str = ""
    email_password_env: str = ""  #: the variable with the SMTP password
    email_tls: bool = True  #: STARTTLS; a server that cannot do it fails the send
    committed: frozenset[str] = frozenset()


def consent_subject(kind: str) -> str:
    return f"channel-consent:{kind}"


def consent_digest(kind: str) -> str:
    """What a consent is of: the kind, and nothing that changes per use."""
    return content_digest(consent_subject(kind))


def consented(state: Any, kind: str) -> bool:
    return approval.check(state, consent_subject(kind), consent_digest(kind)).ok


def grant_consent(log, kind: str, *, note: str = "", requested_agent: str = "") -> None:
    """A person consents to one kind of network channel. Refused under an agent identity."""
    if kind not in set(KINDS.values()):
        raise ValueError(f"no consent kind {kind!r}; the kinds are {sorted(set(KINDS.values()))}")
    approval.grant(
        log,
        consent_subject(kind),
        consent_digest(kind),
        note=note,
        requested_agent=requested_agent,
    )


def _scrub(cfg: Config):
    """The redaction every outbound word goes through: the `upstream` profile (what leaves)."""
    profile = redactor("upstream", cfg)
    return lambda text: profile.text(text).text


def _harness(log, cfg: Config, repo: Path | None) -> N.HarnessChannel:
    def payload(a: Approval) -> dict[str, Any]:
        return H.payload(E.get(fold(log.read_all(), strict=False), a.ask_id) or {}, cfg)

    def read(a: Approval) -> Answer | None:
        ok, message = H.attest(log, cfg, a.ask_id, repo=repo)
        if ok:
            row = E.get(fold(log.read_all(), strict=False), a.ask_id) or {}
            return Answer(
                N.ANSWERED,
                row.get("outcome", ""),
                who=row.get("answered_by", ""),
                how=N.HarnessChannel.name,
                digest=a.digest,
                proof=row.get("proof", ""),
                detail=message,
                recorded=True,
            )
        if message.startswith(H.WAITING):
            return None
        return Answer(N.UNAVAILABLE, how=N.HarnessChannel.name, detail=message)

    return N.HarnessChannel(payload=payload, read=read)


def _blocked(state: Any, settings: Settings, name: str) -> str:
    """Why the network adapter ``name`` may not be used here, or ""."""
    kind = KINDS[name]
    if not consented(state, kind):
        return f"no consent for {kind} channels: a person grants it once per kind"
    private = ADDRESSES if name == "email" else ("endpoints",)
    if exposed := sorted(settings.committed.intersection(private)):
        return f"{', '.join(exposed)} sit in a committed layer: addresses stay machine-local"
    return ""


def _email(settings: Settings, env: ENV.Env, scrub) -> Any:
    n = settings
    secret = env.get(n.email_password_env, "") if n.email_password_env else ""
    return N.CHANNELS["email"](
        host=n.email_host,
        port=n.email_port,
        sender=n.email_from,
        to=n.email_to,
        user=n.email_user,
        password=secret or "",
        tls=n.email_tls,
        timeout_s=n.send_timeout_s,
        scrub=scrub,
    )


def settings_from(cfg: Config) -> Settings:
    """Build `Settings` from the `[notify]` knobs; ``committed`` names the fields whose value came
    from a committed layer, so an address there makes its adapter unavailable (r-secrets)."""
    n = cfg.notify
    fields = (
        "channel", "ask_channel", "endpoints", "token_env", "send_timeout_s", "ask_timeout_s",
        "ask_attempts", "email_host", "email_port", "email_from", "email_to", "email_user",
        "email_password_env", "email_tls",
    )
    committed = frozenset(f for f in fields if cfg.sources.get(f"notify.{f}") == "file")
    return Settings(
        channel=n.channel,
        ask_channel=n.ask_channel,
        endpoints=dict(n.endpoints),
        token_env=dict(n.token_env),
        send_timeout_s=n.send_timeout_s,
        ask_timeout_s=n.ask_timeout_s,
        ask_attempts=n.ask_attempts,
        email_host=n.email_host,
        email_port=n.email_port,
        email_from=n.email_from,
        email_to=n.email_to,
        email_user=n.email_user,
        email_password_env=n.email_password_env,
        email_tls=n.email_tls,
        committed=committed,
    )


def open_channel(
    log,
    cfg: Config,
    name: str,
    *,
    settings: Settings | None = None,
    repo: Path | None = None,
    http: HTTP.HttpClient | None = None,
    env: ENV.Env | None = None,
    proc: Proc | None = None,
    person: str = "",
) -> HumanChannel:
    """The adapter ``name``, built from ``settings``, or an `UnavailableChannel` that says why it
    may not be used. An unknown name raises the registry's `UnknownName`, which lists those known."""
    cls = N.CHANNELS[name]
    n = settings or settings_from(cfg)
    if name == "terminal":
        return cls(
            person=person or approval.os_user(),
            attempts=n.ask_attempts,
            timeout_s=n.ask_timeout_s,
        )
    if name == "harness":
        return _harness(log, cfg, repo)
    if name == "desktop":
        return cls(
            proc=proc or P,
            platform=sys.platform,
            timeout_s=n.send_timeout_s,
            scrub=_scrub(cfg),
        )
    why = _blocked(fold(log.read_all(), strict=False), n, name)
    if why:
        return N.UnavailableChannel(name, why)
    env = env or ENV.DEFAULT
    if name == "email":
        return _email(n, env, _scrub(cfg))
    token = env.get(n.token_env[name], "") if name in n.token_env else ""
    return cls(
        url=n.endpoints.get(name, ""),
        http=http or HTTP.default_client(),
        timeout_s=n.send_timeout_s,
        token=token or "",
        scrub=_scrub(cfg),
    )
