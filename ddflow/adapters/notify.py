"""The `HumanChannel` port: reach a person, ask them something, relay an approval (ADP-notify-ask).

Home of the `notify` family. The port is `HumanChannel`, declared below; the fake is
`ddflow.adapters.fakes.notify.FakeHumanChannel`. A family with several adapters keeps them in
`CHANNELS`, keyed by ``name``: the local ones live here, the ones that leave the machine (and the
desktop, which starts a process) in `ddflow.adapters.notify_outbound`, loaded when first named.

Three calls, one result shape each:

* ``notify(message, severity)`` -- a `Delivery`: delivered, failed, or unavailable (could not try).
* ``ask(question, options)`` -- an `Answer`: answered with who and how, pending (put to the person
  on a channel whose answer arrives elsewhere), failed (the other end refused it), or unavailable.
* ``relay(approval)`` -- the same for an `Approval`, echoing the digest the person was shown.

A channel COLLECTS; it never decides. It reads no configuration, checks no consent and holds no
identity check, because this layer imports nothing above it: `ddflow.services.ask.relay` records
what a channel collected through `engine.answer`, the one place that compares the digest and
refuses an agent identity. An `Answer` is a report of what happened on the channel, not authority.
Unavailable is never answered: a channel that could not reach anyone says so.
"""

from __future__ import annotations

import contextlib
import os
import select
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, TextIO, runtime_checkable

from . import clock
from .ports import port
from .registry import Registry

try:
    import termios
except ImportError:  # Windows: the port still loads, and its terminal reports unavailable
    termios = None  # type: ignore[assignment]

INFO, WARNING, CRITICAL = "info", "warning", "critical"
SEVERITIES = (INFO, WARNING, CRITICAL)
#: A `Delivery`'s status; an `Answer` has `FAILED`, `UNAVAILABLE` and the two below.
DELIVERED, FAILED, UNAVAILABLE = "delivered", "failed", "unavailable"
ANSWERED, PENDING = "answered", "pending"
#: What an approval is answered with: the ask engine's words (a test pins that they match).
APPROVED, DENIED = "approved", "denied"
APPROVAL_OPTIONS = (APPROVED, DENIED)
#: What a broken output stream raises: an `OSError`, or a `ValueError` for a closed file or a
#: character its encoding cannot hold.
STREAM_ERRORS = (OSError, ValueError)


@dataclass(frozen=True)
class Delivery:
    """How one notification ended. ``detail`` says why when it did not arrive; it never holds an
    endpoint or a credential."""

    channel: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == DELIVERED


@dataclass(frozen=True)
class Approval:
    """What a person is asked to approve: the ask's id, its text and its digest. ``code`` is the
    one-time code a remote answer must present, shown to the person and to nobody else."""

    ask_id: str
    text: str
    digest: str
    code: str = ""


@dataclass(frozen=True)
class Answer:
    """What a channel got. ``text`` is the option or the free text, ``who`` and ``how`` the person
    and the channel, ``digest`` what they were shown. ``recorded`` says the channel's own verifier
    wrote the answer to the log already (the harness bridge), ``proof`` that verifier's evidence;
    ``courier`` is what an agent must hand on for a pending ask to reach the person."""

    status: str
    text: str = ""
    who: str = ""
    how: str = ""
    digest: str = ""
    code: str = ""
    proof: str = ""
    detail: str = ""
    recorded: bool = False
    courier: Mapping[str, Any] = field(default_factory=dict)


def check_severity(severity: str) -> None:
    """Raise for a severity outside `SEVERITIES`: the caller's bug, on every adapter alike."""
    if severity not in SEVERITIES:
        raise ValueError(f"severity is one of {', '.join(SEVERITIES)}; got {severity!r}")


def approval_text(approval: Approval) -> str:
    """The one wording of an approval on a channel: its text, id, digest and any one-time code."""
    code = f"\none-time code: {approval.code}" if approval.code else ""
    return f"{approval.text}\napproval {approval.ask_id}, digest {approval.digest}{code}"


def question_text(question: str, options: Sequence[str]) -> str:
    """A question with its options, for a channel that can only show text."""
    return f"{question}\noptions: {', '.join(options)}" if options else question


@port
@runtime_checkable
class HumanChannel(Protocol):
    """Reach a person. Home: `ddflow.adapters.notify`; adapters in `CHANNELS`, fake `FakeHumanChannel`.

    An adapter also has a ``name`` (its registry key, and the ``how`` of its answers; not declared
    here: the port contract reserves that for `Forge`). No method raises for the channel's own
    trouble: that is a status. A severity outside `SEVERITIES` is a `ValueError` on every adapter.
    """

    def notify(self, message: str, severity: str = "info") -> Delivery:
        """Tell the person something; nothing comes back."""

    def ask(self, question: str, options: Sequence[str] = ()) -> Answer:
        """Put a question, with `options` or for free text; an `Answer`."""

    def relay(self, approval: Approval) -> Answer:
        """Put an approval to the person; an `Answer` with `APPROVED` or `DENIED` when answered."""


#: A reader takes the seconds to wait and returns a line, or None when nothing came.
Reader = Callable[[float], "str | None"]


def _forget_typed(fd: int) -> None:
    """Drop what was typed and never finished, so it cannot start the next question's answer."""
    if termios is not None:
        with contextlib.suppress(termios.error, OSError):
            termios.tcflush(fd, termios.TCIFLUSH)


def stdin_reader(timeout_s: float) -> str | None:
    """A line from a real terminal, or None. Refuses a stdin that is no terminal without touching
    it: over MCP stdin IS the JSON-RPC session, and one read would eat the protocol's bytes.
    Reads the descriptor itself, so no buffer hides a line from the deadline. A terminal in its
    normal mode hands over one finished line per read and is not ready before the newline; a line
    never finished is cut at ``timeout_s`` and forgotten."""
    stream = sys.stdin
    if stream is None or stream.closed or not stream.isatty():
        return None
    fd = stream.fileno()
    deadline = clock.DEFAULT.monotonic() + max(0.0, timeout_s)
    data = b""
    while b"\n" not in data:
        left = deadline - clock.DEFAULT.monotonic()
        if not select.select([fd], [], [], max(0.0, left))[0]:
            _forget_typed(fd)
            return None
        chunk = os.read(fd, 1)  # a byte at a time: what follows the newline stays for the next
        if not chunk:
            break
        data += chunk
    return data.decode("utf-8", "replace") or None


def _plain(text: str) -> str:
    """Text with its control characters gone, lines kept: the words may be an agent's, and an
    escape sequence must not repaint what the person reads."""
    return "".join(c for c in text if c in "\n\t" or c.isprintable())


def pick(line: str, options: Sequence[str]) -> str:
    """The option ``line`` names by label (case-blind) or number; the line itself when free text
    is asked for; "" when it is neither."""
    text = line.strip()
    if not options:
        return text
    for option in options:
        if option.lower() == text.lower():
            return option
    if text.isdecimal() and 1 <= int(text) <= len(options):
        return options[int(text) - 1]
    return ""


class TerminalChannel:
    """A person at this process's terminal. Writes to stderr by default (stdout is the MCP
    protocol's). ``attempts`` and ``timeout_s`` bound one question; ``reader`` and ``out`` are the
    seams a test scripts. An answer here is a terminal's word, level 1 in the ask engine."""

    name = "terminal"

    def __init__(
        self,
        *,
        person: str,
        attempts: int,
        timeout_s: float,
        reader: Reader = stdin_reader,
        out: TextIO | None = None,
    ) -> None:
        self.person = person
        self.attempts = attempts
        self.timeout_s = timeout_s
        self._read = reader
        self._out = out

    def _say(self, text: str) -> None:
        out = self._out or sys.stderr
        if out is None:  # `print(file=None)` would write to stdout, which is the protocol's
            raise OSError("this process has no stderr")
        print(_plain(text), file=out, flush=True)

    def notify(self, message: str, severity: str = INFO) -> Delivery:
        check_severity(severity)
        try:
            self._say(f"[{severity}] {message}")
        except STREAM_ERRORS as exc:  # the channel's trouble, not the caller's
            return Delivery(self.name, UNAVAILABLE, f"nothing to write to ({type(exc).__name__})")
        return Delivery(self.name, DELIVERED)

    def ask(self, question: str, options: Sequence[str] = ()) -> Answer:
        try:
            return self._collect(question, options)
        except STREAM_ERRORS as exc:
            return Answer(
                UNAVAILABLE, how=self.name, detail=f"terminal gone ({type(exc).__name__})"
            )

    def _collect(self, question: str, options: Sequence[str]) -> Answer:
        self._say(question)
        for number, option in enumerate(options, 1):
            self._say(f"  {number}. {option}")
        for _ in range(max(1, self.attempts)):
            line = self._read(self.timeout_s)
            if line is None:
                return Answer(UNAVAILABLE, how=self.name, detail="no answer on this terminal")
            if picked := pick(line, options):
                return Answer(ANSWERED, picked, who=self.person, how=self.name)
            if options:
                self._say(f"answer with one of: {', '.join(options)}")
        return Answer(
            UNAVAILABLE, how=self.name, detail=f"no usable answer in {max(1, self.attempts)} tries"
        )

    def relay(self, approval: Approval) -> Answer:
        got = self.ask(approval_text(approval), APPROVAL_OPTIONS)
        return replace(got, digest=approval.digest) if got.status == ANSWERED else got


def ask_user_question(question: str, options: Sequence[str]) -> dict[str, Any]:
    """A plain question as the harness's AskUserQuestion input."""
    return {
        "questions": [
            {
                "question": question,
                "header": "Question",
                "multiSelect": False,
                "options": [{"label": o, "description": o} for o in options],
            }
        ]
    }


class HarnessChannel:
    """The operator's own chat, reached through an agent's AskUserQuestion tool (r-approval-chat-channel).

    ddflow cannot call that tool: it hands the agent the exact input to send (`Answer.courier`),
    and the answer is read from what the HARNESS wrote, never from the agent. ``payload`` builds
    that input for an approval; ``read`` looks for the answer and returns an `Answer`, or None
    while there is none. Both are given by `ddflow.services.ask.channels`, over `approvals ask`
    and `approvals attest`, so ``read`` records the answer itself (`Answer.recorded`).
    """

    name = "harness"

    def __init__(
        self,
        *,
        payload: Callable[[Approval], Mapping[str, Any]],
        read: Callable[[Approval], Answer | None],
    ) -> None:
        self._payload = payload
        self._find = read

    def notify(self, message: str, severity: str = INFO) -> Delivery:
        check_severity(severity)
        return Delivery(self.name, UNAVAILABLE, "a chat has no push: say it in your reply")

    def ask(self, question: str, options: Sequence[str] = ()) -> Answer:
        return Answer(
            PENDING,
            how=self.name,
            detail="put this to the person with AskUserQuestion",
            courier=ask_user_question(question, options),
        )

    def relay(self, approval: Approval) -> Answer:
        found = self._find(approval)
        if found is not None:
            return found
        return Answer(
            PENDING,
            how=self.name,
            digest=approval.digest,
            detail="put this to the person with AskUserQuestion, then relay again",
            courier=self._payload(approval),
        )


class UnavailableChannel:
    """A channel that may not be used here, and says why on every call (no consent, no endpoint,
    an endpoint in a committed layer). Nothing is sent."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def notify(self, message: str, severity: str = INFO) -> Delivery:
        check_severity(severity)
        return Delivery(self.name, UNAVAILABLE, self.reason)

    def ask(self, question: str, options: Sequence[str] = ()) -> Answer:
        return Answer(UNAVAILABLE, how=self.name, detail=self.reason)

    def relay(self, approval: Approval) -> Answer:
        return Answer(UNAVAILABLE, how=self.name, detail=self.reason)


#: Adapter classes by name. Building one takes what its class says: the settings are the caller's
#: (`ddflow.services.ask.channels` reads the knobs), an adapter reads no configuration.
CHANNELS: Registry[type] = Registry("human channel")
CHANNELS.register(TerminalChannel, TerminalChannel.name)
CHANNELS.register(HarnessChannel, HarnessChannel.name)
for _name, _cls in (
    ("desktop", "DesktopChannel"),
    ("email", "EmailChannel"),
    ("n8n", "N8nChannel"),
    ("openclaw", "OpenClawChannel"),
    ("slack", "SlackChannel"),
    ("teams", "TeamsChannel"),
    ("webhook", "WebhookChannel"),
):
    CHANNELS.lazy(_name, f"ddflow.adapters.notify_outbound:{_cls}")
