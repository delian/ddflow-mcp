"""What an operation did — the ONE description of a result.

Every service operation returns an `Outcome`. The machine surface is its `data`; the
human surface is **derived from that same data** by exactly one renderer in
`views.human`. Neither surface describes the result independently.

That is the whole point. Before this, each command built its JSON view and its human
view inline and separately — 20 hand-rolled `if c.json` branches and 195 bare `print`
calls across one 4,000-line module — and the project has already shipped the bug that
produces. Twice in one function, and the comment is still in the source:

    # The coverage gap must reach BOTH surfaces. It used to print only in human mode,
    # so an agent driving over MCP -- which is always JSON -- completed an item and was
    # never told that a gate had not run.

Two views of one result, hand-kept in sync, drift in the direction of whichever one the
author was looking at. Deriving one from the other makes the drift unrepresentable.

`exit` carries the vocabulary the whole package speaks, at every layer and on both
surfaces:

    0  healthy        1  real failure
    2  could not run / nothing to do        3  coordination refused

`2` is never collapsed into `0`. "No data" is reported as itself, never as "no problem"
— that rule is why `ddflow next` with an empty queue and `ddflow next` with a blocked
queue are distinguishable without reading the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NamedTuple

OK = 0
FAIL = 1
NOTHING = 2
REFUSED = 3


class Verdict(NamedTuple):
    """What a check says: an exit from the vocabulary above and the text for whoever has to
    act on it. A tuple, so a caller that unpacks ``code, msg = ...`` or compares to
    ``(0, "")`` is unchanged, with its parts named for the ones that read ``.exit``."""

    exit: int
    message: str = ""


#: Human labels, for error messages that need to name a code.
EXIT_NAMES = {OK: "ok", FAIL: "failed", NOTHING: "nothing", REFUSED: "refused"}
#: The exit a Ctrl-C ends a command with: 128 + SIGINT, as a shell reports it.
INTERRUPTED = 130


#: Exits for classes this bottom layer cannot import, by qualified name; a class that can
#: declare `exit_code` itself does so instead. tests/test_exit_mapping.py imports each
#: one, so a rename fails there rather than silently dropping the entry.
_EXIT_BY_NAME = {"ddflow.infra.worktree.GitError": FAIL}


def declared_exit(exc: BaseException) -> int | None:
    """The exit `exc`'s class declares -- its `exit_code`, or its entry in `_EXIT_BY_NAME`
    -- else None. The one answer to "does this class say its own exit?", for `exit_for`
    and for MCP's bad-arguments reading, which a declared exit overrides."""
    code = getattr(type(exc), "exit_code", None)
    if isinstance(code, int):
        return code
    for cls in type(exc).__mro__:
        if (named := _EXIT_BY_NAME.get(f"{cls.__module__}.{cls.__qualname__}")) is not None:
            return named
    return None


def exit_for(exc: BaseException) -> int | None:
    """The exit an exception raised by a command maps to -- ONE table for the CLI and MCP
    (B5f3a650c40), which disagreed: MCP called every Key/Type/ValueError "bad arguments",
    so a refusal that subclasses ValueError (`ReviewerRefused`) read as a malformed call,
    and a `LeaseError` as an internal error. A class declares its own exit with
    `exit_code` (every refusal: `REFUSED`), or is named in `_EXIT_BY_NAME` (`GitError`:
    `FAIL`); a ValueError or KeyError is an error in what was asked (`FAIL`); None means
    a bug, which each surface lets surface as one. One surface-specific reading stays with MCP: a Key/Type/
    ValueError whose class declares no exit is a malformed call there (JSON arguments are
    untyped), where argparse has typed the CLI's; a declared one keeps its own exit."""
    if isinstance(exc, KeyboardInterrupt):
        return INTERRUPTED
    if (declared := declared_exit(exc)) is not None:
        return declared
    if isinstance(exc, (ValueError, KeyError)):
        return FAIL
    return None


@dataclass
class Outcome:
    """One operation's result.

    ``kind`` selects the renderer in `views.human`; it is not free text. A kind with no
    renderer is a result that cannot be shown to a person, and `tests/test_views.py`
    refuses to let one exist — the same shape as `events.KINDS` being derived from
    `model.HANDLERS` so an event kind nothing interprets cannot exist either.
    """

    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    exit: int = OK

    #: Set when the operation refused or failed: the reason, addressed to whoever has
    #: to act on it. Kept out of `data` because every renderer shows it the same way and
    #: because a caller checking "did this work" should not have to know the kind.
    reason: str = ""

    def body(self, payload: str | tuple[str, ...] = "") -> Any:
        """The WIRE body: what `--json` prints and what the MCP tool returns.

        `data` carries everything either surface might want — counts for a human
        summary, flags for a warning, the rows themselves. The body a caller has always
        received is usually a SUBSET of that, and migrating an operation to this layer
        must not redefine it: `ddflow_loops` silently went from a JSON array to an
        object and broke two demo scenarios before this was pinned.

        So the tool declares which part is the body, and both surfaces ask the Outcome
        for it rather than each assembling its own:

            ""                  the whole `data` dict
            "rows"              that key's value — an array stays an array
            ("id", "by")        a projection, for bodies that are a small object

        One implementation, because the CLI's `--json` and the MCP body being
        the same parsed value (MCP's text is compact, the CLI's indented) is the property `MIGRATED_WIRE_SHAPES` checks, and two copies of
        the projection rule is how they would come apart. The one deliberate exception
        is an operation that asks for less on MCP: `status` cuts its lists there and
        says so in `truncated` (Bd6aa9ffde9), so the two agree only while no list
        passes `STATUS_LIST_LIMIT` -- pinned by tests/test_status_bound.py. The five reads
        `surfaces/mcp_bound.py` projects (next, show, recall, decision_list, progress) are
        the same kind of exception, pinned by tests/test_mcp_payload_bound.py.
        """
        if not payload:
            # `_`-prefixed keys are for the LOCAL renderer and never cross the wire.
            #
            # A prose view frequently needs the objects the operation already built —
            # `status` wants `completed_at` to sort by and the blocked ids, `show` wants
            # the gate-status object's `render()`. Recomputing them in the surface means
            # folding the log twice for one answer, which is the thing this layer exists
            # to stop; carrying them in `data` means they would be serialised to every
            # MCP caller as unreadable repr strings. One naming rule settles it, in the
            # one place that decides what a body is.
            return {k: v for k, v in self.data.items() if not k.startswith("_")}
        if isinstance(payload, str):
            return self.data[payload]
        # `.get`, not `[...]`. A projection has to produce the SAME SHAPE on every exit
        # code: an operation that refuses often has less to say than one that succeeded,
        # and `workflow drop` on a gate that is in no pipeline returns `nothing` with no
        # `applied` key at all. Indexing turned that refusal into a KeyError inside the
        # tool dispatcher -- so the caller got a crash where the whole point was to
        # deliver "there was nothing to drop". A stable shape with an explicit `null` is
        # what a consumer can branch on.
        return {k: self.data.get(k) for k in payload}

    @property
    def ok(self) -> bool:
        return self.exit == OK

    def __bool__(self) -> bool:
        return self.ok


#: Field names the Outcome takes itself. An operation whose WIRE shape includes one of
#: these cannot spread `**data` into a helper: `gate verify` and `decision supersede` both
#: carry a `reason` on the wire, meaning different things from the Outcome's `reason`.
#:
#: WHERE THIS IS CAUGHT DIFFERS, and it is worth knowing which message you will get.
#: `failed`, `refused` and `nothing` declare `reason` as a parameter, so PYTHON rejects
#: the duplicate first -- "failed() got multiple values for argument 'reason'", which is
#: true and says nothing about the remedy. `_check` cannot run before that and does not
#: try. It covers `ok()`, which has no such parameter and would otherwise swallow a
#: `reason=` into `data` silently, and `exit`/`kind` wherever they are not positional.
#:
#: Either way the remedy is the same: build the Outcome directly.
_RESERVED = ("kind", "reason", "exit")


def _check(fn: str, data: dict[str, Any]) -> None:
    clash = [k for k in _RESERVED if k in data]
    if clash:
        raise TypeError(
            f"{fn}() cannot take {clash} as wire fields: they are the Outcome's own. "
            f"Build it directly — `O.Outcome(kind=..., data=data, exit=..., reason=...)` "
            f"— so the wire field and the Outcome's field stay separate."
        )


def ok(kind: str, **data: Any) -> Outcome:
    _check("ok", data)
    return Outcome(kind=kind, data=data)


def nothing(kind: str, reason: str = "", **data: Any) -> Outcome:
    """Nothing to do. NOT a failure, and never rendered as success."""
    _check("nothing", data)
    return Outcome(kind=kind, data=data, exit=NOTHING, reason=reason)


def refused(kind: str, reason: str, **data: Any) -> Outcome:
    """Coordination said no: a lease is held, a dependency is open, a gate has not run.

    Distinct from `failed` because the remedy is different — a refusal is answered by
    waiting, re-ordering, or satisfying the condition, and an agent that cannot tell the
    two apart retries the wrong one.
    """
    _check("refused", data)
    return Outcome(kind=kind, data=data, exit=REFUSED, reason=reason)


def failed(kind: str, reason: str, **data: Any) -> Outcome:
    _check("failed", data)
    return Outcome(kind=kind, data=data, exit=FAIL, reason=reason)
