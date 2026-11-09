"""The `[notify]` section: the channel settings a person can tune, notably `services/ask/channels`.

Re-exported from `ddflow.config`, which assembles `Config` from every section (ADP-notify-knobs)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ._docs import declare, int_at_least, knob

#: Where a notification goes: `terminal` is always safe; every other choice reaches outward
#: (the channel-consent kinds, ADP-notify-ask).
_CHANNELS = ("terminal", "email", "n8n", "openclaw", "slack", "teams", "webhook")
#: Where questions and approvals go: `harness` is in-band; the rest reach out.
_ASK_CHANNELS = ("harness", "email", "n8n", "openclaw", "slack", "teams", "webhook")
_OUTWARD = frozenset({"email", "n8n", "openclaw", "slack", "teams", "webhook"})


@declare("notify")
@dataclass
class NotifyConfig:
    """The channels notifications and questions go through, and the credentials they use."""

    channel: str = knob(
        "terminal",
        doc="Where notifications go. 'terminal' writes to the operator's terminal; each other choice is one network channel, reach outward (r-consent-outward), and needs a grant before any adapter will send.",
        choices=_CHANNELS,
        strictest=("terminal", "a typo across clones is local, never a network send"),
        outward=_OUTWARD,
    )
    ask_channel: str = knob(
        "harness",
        doc="Where questions and approvals go. 'harness' is the in-band agent chat; each other choice is one network channel and needs a grant first.",
        choices=_ASK_CHANNELS,
        strictest=("harness", "a typo across clones stays local, never a network send"),
        outward=_OUTWARD,
    )
    endpoints: Mapping[str, str] = knob(
        factory=dict,
        doc="Adapter -> its whole hook URL. A URL here in a committed layer makes that adapter unavailable.",
    )
    token_env: Mapping[str, str] = knob(
        factory=dict,
        doc="Adapter -> the variable holding its token (a secret; keep it in the local layer).",
    )
    send_timeout_s: float = knob(
        10.0,
        doc="How long one notification send may take (a webhook POST, a mail exchange, a desktop command) before it is dropped.",
    )
    ask_timeout_s: float = knob(
        300.0,
        doc="How long the terminal waits for one line of an answer before it gives up that attempt.",
    )
    ask_attempts: int = knob(
        3,
        doc="Unusable answers the terminal takes before it gives up on a question.",
        check=int_at_least(1),
    )
    email_host: str = knob(
        "",
        doc="The SMTP host mail is sent through (an address here in a committed layer makes the email adapter unavailable).",
    )
    email_port: int = knob(587, doc="The SMTP port.", check=int_at_least(1))
    email_from: str = knob(
        "",
        doc="The envelope sender (an address here in a committed layer makes the email adapter unavailable).",
    )
    email_to: str = knob(
        "",
        doc="The recipient (an address here in a committed layer makes the email adapter unavailable).",
    )
    email_user: str = knob("", doc="The SMTP login (a secret; keep it in the local layer).")
    email_password_env: str = knob("", doc="The variable holding the SMTP password.")
    email_tls: bool = knob(
        True, doc="STARTTLS on the SMTP connection; a host that cannot do it fails the send."
    )
