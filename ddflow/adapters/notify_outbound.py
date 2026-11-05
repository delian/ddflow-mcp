"""The `HumanChannel` adapters that leave this process: webhooks, chat, email and the desktop.

Part of the `notify` family; the port and how a channel behaves are in `ddflow.adapters.notify`.
None of these can bring an answer back on the call: a question or an approval is POSTED, the
result is `PENDING` when the post arrived, and the answer comes in through the ask engine's own
transports (P-remote.ask). The desktop cannot take one at all, so it reports `UNAVAILABLE`.

What this module does not do: decide consent, read configuration or hold a credential past the
call. `ddflow.services.ask.channels` checks consent once per kind and passes the endpoint, token,
timeout and ``scrub`` (the redaction that runs on every word that leaves; a digest, an id and a
one-time code are the protocol's, not prose, so they are not scrubbed).

Failures follow D-adapters-answers-3: offline is `UNAVAILABLE` (the channel could not be tried), a
refusal from the other end is `FAILED`. A chat webhook's URL IS its secret, so no detail quotes
it: an error here says its class and status, never the exception's text.
"""

from __future__ import annotations

import json
import smtplib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from .http import (
    HttpClient,
    HttpConnectionError,
    HttpError,
    HttpSchemeError,
    HttpStatusError,
    HttpTimeout,
)
from .notify import (
    APPROVAL_OPTIONS,
    CRITICAL,
    DELIVERED,
    FAILED,
    INFO,
    PENDING,
    UNAVAILABLE,
    WARNING,
    Answer,
    Approval,
    Delivery,
    approval_text,
    check_severity,
    question_text,
)
from .proc import Proc

__all__ = [
    "DesktopChannel",
    "EmailChannel",
    "N8nChannel",
    "OpenClawChannel",
    "SlackChannel",
    "TeamsChannel",
    "WebhookChannel",
]

APP = "ddflow"
#: What the Adaptive Card a Teams workflow webhook takes is declared as (research R2f96193444:
#: Office 365 connectors were retired in May 2026; the Workflows trigger takes this form).
CARD_TYPE = "application/vnd.microsoft.card.adaptive"
CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
CARD_VERSION = "1.2"
#: The reply is ignored: only its status counts, so no more of it is read than a status page.
REPLY_BYTES = 4096


def _same(text: str) -> str:
    return text


@dataclass(frozen=True)
class Message:
    """One thing to say, before it takes a target's wire shape. ``text`` is the words a person
    reads; the rest are protocol fields for a target that can use them."""

    event: str
    text: str
    severity: str = ""
    options: tuple[str, ...] = ()
    ask_id: str = ""
    digest: str = ""
    code: str = ""


def _as_pending(name: str, done: Delivery, digest: str = "") -> Answer:
    """What a post means for a question: the person has it, or the delivery's failure stands."""
    if done.ok:
        return Answer(
            PENDING, how=name, digest=digest, detail="posted: the answer arrives elsewhere"
        )
    return Answer(done.status, how=name, detail=done.detail)


class _Outbound:
    """What every outbound adapter shares: turn the three calls into `_send` of one `Message`."""

    name = ""

    def __init__(self, *, scrub: Callable[[str], str] = _same) -> None:
        self._scrub = scrub

    def _send(self, message: Message) -> Delivery:
        raise NotImplementedError

    def notify(self, message: str, severity: str = INFO) -> Delivery:
        check_severity(severity)
        return self._send(Message("notify", self._scrub(message), severity))

    def ask(self, question: str, options: Sequence[str] = ()) -> Answer:
        clean = tuple(self._scrub(o) for o in options)
        text = question_text(self._scrub(question), clean)
        return _as_pending(self.name, self._send(Message("ask", text, options=clean)))

    def relay(self, approval: Approval) -> Answer:
        shown = Approval(
            approval.ask_id, self._scrub(approval.text), approval.digest, approval.code
        )
        sent = self._send(
            Message(
                "approval",
                approval_text(shown),
                options=APPROVAL_OPTIONS,
                ask_id=approval.ask_id,
                digest=approval.digest,
                code=approval.code,
            )
        )
        return _as_pending(self.name, sent, approval.digest)


class WebhookChannel(_Outbound):
    """A generic JSON webhook: one POST through the `HttpClient` port. ``token`` becomes a bearer
    header when given. A target with its own wire shape overrides `body`."""

    name = "webhook"

    def __init__(
        self,
        *,
        url: str,
        http: HttpClient,
        timeout_s: float,
        token: str = "",
        scrub: Callable[[str], str] = _same,
    ) -> None:
        super().__init__(scrub=scrub)
        self._url = url
        self._http = http
        self._timeout_s = timeout_s
        self._token = token

    def body(self, message: Message) -> Mapping[str, Any]:
        out: dict[str, Any] = {"event": message.event, "text": message.text}
        if message.severity:
            out["severity"] = message.severity
        if message.options:
            out["options"] = list(message.options)
        for key in ("ask_id", "digest", "code"):
            if value := getattr(message, key):
                out[key] = value
        return out

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _send(self, message: Message) -> Delivery:
        if not self._url:
            return Delivery(self.name, UNAVAILABLE, "no endpoint is configured")
        data = json.dumps(self.body(message)).encode("utf-8")
        try:
            self._http.request(
                "POST",
                self._url,
                headers=self._headers(),
                body=data,
                timeout=self._timeout_s,
                max_bytes=REPLY_BYTES,
            )
        except HttpStatusError as exc:
            return Delivery(self.name, FAILED, f"the endpoint answered {exc.status}")
        except HttpSchemeError:
            return Delivery(self.name, UNAVAILABLE, "the endpoint is not an http(s) address")
        except (HttpConnectionError, HttpTimeout) as exc:
            return Delivery(
                self.name, UNAVAILABLE, f"the endpoint could not be reached ({exc.kind})"
            )
        except HttpError as exc:
            return Delivery(self.name, FAILED, f"the exchange broke ({exc.kind})")
        return Delivery(self.name, DELIVERED)


class N8nChannel(WebhookChannel):
    """An n8n workflow's Webhook trigger: the generic body, which a workflow maps as it likes."""

    name = "n8n"


class SlackChannel(WebhookChannel):
    """A Slack incoming webhook: one `text` field."""

    name = "slack"

    def body(self, message: Message) -> Mapping[str, Any]:
        return {"text": message.text}


class TeamsChannel(WebhookChannel):
    """A Teams Workflows webhook: the text as an Adaptive Card."""

    name = "teams"

    def body(self, message: Message) -> Mapping[str, Any]:
        block = {"type": "TextBlock", "text": message.text, "wrap": True}
        card = {
            "$schema": CARD_SCHEMA,
            "type": "AdaptiveCard",
            "version": CARD_VERSION,
            "body": [block],
        }
        return {"type": "message", "attachments": [{"contentType": CARD_TYPE, "content": card}]}


class OpenClawChannel(WebhookChannel):
    """An OpenClaw gateway's ``/hooks/agent``: `message` and a label; the URL is the whole hook
    address and the hook token is the bearer (OpenClaw refuses a token in the query string)."""

    name = "openclaw"

    def body(self, message: Message) -> Mapping[str, Any]:
        return {"message": message.text, "name": APP}


class EmailChannel(_Outbound):
    """Mail through an SMTP server. STARTTLS unless ``tls`` is off, and a server that cannot do it
    fails the send: it never falls back to plain text. The subject is fixed, so no text of a
    message can reach a header. ``smtp`` is the seam a test scripts."""

    name = "email"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        to: str,
        timeout_s: float,
        user: str = "",
        password: str = "",
        tls: bool = True,
        scrub: Callable[[str], str] = _same,
        smtp: Callable[..., Any] = smtplib.SMTP,
    ) -> None:
        super().__init__(scrub=scrub)
        self.host = host
        self.port = port
        self.sender = sender
        self.to = to
        self.user = user
        self.password = password
        self.tls = tls
        self._timeout_s = timeout_s
        self._smtp = smtp

    def _mail(self, message: Message) -> EmailMessage:
        mail = EmailMessage()
        mail["Subject"] = f"{APP}: {message.severity or message.event}"
        mail["From"] = self.sender
        mail["To"] = self.to
        mail.set_content(message.text)
        return mail

    def _send(self, message: Message) -> Delivery:
        if not (self.host and self.sender and self.to):
            return Delivery(
                self.name, UNAVAILABLE, "no mail server, sender or recipient is configured"
            )
        try:
            with self._smtp(self.host, self.port, timeout=self._timeout_s) as server:
                if self.tls:
                    server.starttls()
                if self.user:
                    server.login(self.user, self.password)
                server.send_message(self._mail(message))
        except smtplib.SMTPException as exc:
            return Delivery(self.name, FAILED, f"the mail server refused ({type(exc).__name__})")
        except ValueError:  # an address with a line break, a host that does not encode
            return Delivery(self.name, FAILED, "a mail address or host is malformed")
        except OSError as exc:
            return Delivery(
                self.name,
                UNAVAILABLE,
                f"the mail server could not be reached ({type(exc).__name__})",
            )
        return Delivery(self.name, DELIVERED)


#: `notify-send --urgency` by severity.
URGENCY = {INFO: "low", WARNING: "normal", CRITICAL: "critical"}
#: The text goes in as an argument, so a quote in it is never AppleScript. It follows `--` and the
#: title, so a leading dash is never `-e`, whichever way osascript's parser stops at options.
OSA_SCRIPT = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
)


class DesktopChannel:
    """A desktop notification: `notify-send` on Linux, `osascript` on macOS, through the `Proc`
    port. Other platforms report unavailable (D-adapters-answers-3). A notification takes no
    answer, so `ask` and `relay` report unavailable everywhere."""

    name = "desktop"

    def __init__(
        self,
        *,
        proc: Proc,
        platform: str,
        timeout_s: float,
        scrub: Callable[[str], str] = _same,
    ) -> None:
        self._proc = proc
        self._platform = platform
        self._timeout_s = timeout_s
        self._scrub = scrub

    def _argv(self, message: str, severity: str) -> list[str]:
        if self._platform.startswith("linux"):
            return [
                "notify-send",
                f"--urgency={URGENCY[severity]}",
                f"--app-name={APP}",
                "--",
                APP,
                message,
            ]
        if self._platform == "darwin":
            script = (a for line in OSA_SCRIPT for a in ("-e", line))
            return ["osascript", *script, "--", APP, message]
        return []

    def notify(self, message: str, severity: str = INFO) -> Delivery:
        check_severity(severity)
        argv = self._argv(self._scrub(message), severity)
        if not argv:
            return Delivery(self.name, UNAVAILABLE, f"no desktop notifications on {self._platform}")
        done = self._proc.capture(argv, timeout=self._timeout_s)
        if done.error is not None:
            return Delivery(
                self.name, UNAVAILABLE, f"{argv[0]} could not run ({type(done.error).__name__})"
            )
        if done.returncode != 0:
            return Delivery(self.name, FAILED, f"{argv[0]} exited {done.returncode}")
        return Delivery(self.name, DELIVERED)

    def ask(self, question: str, options: Sequence[str] = ()) -> Answer:
        return Answer(UNAVAILABLE, how=self.name, detail="a desktop notification takes no answer")

    def relay(self, approval: Approval) -> Answer:
        return Answer(UNAVAILABLE, how=self.name, detail="a desktop notification takes no answer")
