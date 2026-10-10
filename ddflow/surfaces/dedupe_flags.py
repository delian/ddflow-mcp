"""How a person answers the add-time duplicate check on the command line.

The check itself is ``api/_dedupe.py`` (decision D-no-duplicates); this is its CLI face,
shared by every add command -- ``task add``, ``phase add``, ``bug found``, ``lesson add``,
``decision add``, ``research add``, ``memory add`` -- so they cannot drift apart:

* the flags ``--new`` / ``--extends ID`` / ``--duplicate-of ID`` / ``--related ID`` (one
  at most) and ``--check`` (a dry run that writes nothing);
* on a terminal, a refused add asks which of them it is;
* anywhere else it exits 3 with the candidates and the commands that would answer it.
"""

from __future__ import annotations

import shlex
import sys
from typing import Any

from ..api._dedupe import Answer
from ..core.outcome import REFUSED, Outcome
from .declared.answer import ANSWER_CLI_PARAMS, candidate_lines


def add_flags(parser) -> None:
    """The answer flags and ``--check`` on one add command's parser: the declared
    parameters (`declared/answer.py`), at most one of them."""
    groups: dict = {}
    for param in ANSWER_CLI_PARAMS:
        param.add_to(parser, groups)


def answer_of(a) -> Answer:
    """The answer the flags give (empty when none), with ``--check`` as a dry run."""
    check = bool(getattr(a, "check", False))
    if getattr(a, "new", False):
        return Answer("new", check_only=check)
    for rel, flag in (
        ("extends", "extends"),
        ("duplicate_of", "duplicate_of"),
        ("related", "related"),
    ):
        target = getattr(a, flag, "") or ""
        if target:
            return Answer(rel, target, check)
    return Answer(check_only=check)


def run(a, c, call) -> Outcome:
    """Run one add (``call(answer) -> Outcome``), asking on a terminal when the check
    refuses it and no flag said what it is."""
    answer = answer_of(a)
    out = call(answer)
    if answer or not _asks(out) or c.json or not _interactive():
        return out
    chosen = ask(out.data["candidates"])
    if chosen is None:
        return Outcome(
            kind=out.kind,
            data=out.data | {"aborted": True},
            exit=REFUSED,
            reason="aborted: nothing was filed",
        )
    return call(chosen)


def _asks(out: Outcome) -> bool:
    return (
        out.exit == REFUSED and bool(out.data.get("candidates")) and bool(out.data.get("options"))
    )


def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


MENU = "[n]ew / [e]xtends # / [d]uplicate of # / [r]elated # / [a]bort"


def ask(rows: list[dict[str, Any]], *, tries: int = 5) -> Answer | None:
    """Prompt for the answer. ``None`` is abort (also end of input, or giving up)."""
    print("This reads like a record that is already filed:")
    print("\n".join(candidate_lines(rows, numbered=True)))
    for _ in range(tries):
        try:
            # Written here, not by input(): on a terminal input() sends its prompt to
            # stderr, and a caller who redirected stderr would be asked nothing.
            sys.stdout.write(f"{MENU}  > ")
            sys.stdout.flush()
            reply = input().strip().lower()
        except EOFError:
            return None
        got = parse_reply(reply, rows)
        if got is not False:
            return got
        print(f"Not understood. One of: {MENU}   (# is the number above, or the record id)")
    return None


def parse_reply(reply: str, rows: list[dict[str, Any]]) -> Answer | bool | None:
    """An Answer, ``None`` for abort, ``False`` when the reply is not one of the menu."""
    words = reply.split()
    if not words:
        return False
    head, rest = words[0], words[1:]
    if head in ("a", "abort", "q"):
        return None
    if head in ("n", "new"):
        return Answer("new")
    rel = {
        "e": "extends",
        "extends": "extends",
        "d": "duplicate_of",
        "duplicate": "duplicate_of",
        "r": "related",
        "related": "related",
    }.get(head)
    if rel is None:
        return False
    if not rest:
        if len(rows) != 1:
            return False
        return Answer(rel, rows[0]["id"])
    ref = rest[-1]
    if ref.isascii() and ref.isdigit() and 1 <= int(ref) <= len(rows):
        return Answer(rel, rows[int(ref) - 1]["id"])
    ids = {r["id"].lower(): r["id"] for r in rows}
    if ref in ids:
        return Answer(rel, ids[ref])
    return False


def commands(argv: list[str], rows: list[dict[str, Any]]) -> list[str]:
    """The invocation again with each answer added: ready to copy. The flag goes BEFORE a
    ``--`` end-of-options separator, which would otherwise swallow it as text."""
    cut = argv.index("--") if "--" in argv else len(argv)
    head, tail = argv[:cut], argv[cut:]
    top = rows[0]["id"]
    answers = [["--new"], ["--extends", top], ["--duplicate-of", top], ["--related", top]]
    return ["ddflow " + shlex.join([*head, *answer, *tail]) for answer in answers]


def settle(a, c, out: Outcome) -> int | None:
    """Print what the check decided and return the exit code; ``None`` when the add
    went its ordinary way and the command should report it as before."""
    data = out.data
    if data.get("check_only"):
        if c.json:
            if out.exit:
                # The body is the same whether the kind is not checked, the index could not
                # be read or nothing matched: the reason says which.
                print(out.reason, file=sys.stderr)
            c.out(out.reason, out.body())
        elif out.exit:
            print(out.reason)
        else:
            print("\n".join(candidate_lines(data["candidates"])))
            if data.get("would_extend"):
                verdict = (
                    f"An add of this is an exact copy: it would be added to "
                    f"{data['would_extend']} without asking, and no new record made"
                )
            elif data.get("would_link"):
                verdict = (
                    f"An add of this is an exact copy of {data['would_link']}, which is "
                    f"claimed or closed: it would be filed as a new record linked to it, "
                    f"without asking"
                )
            elif data["would_ask"]:
                verdict = (
                    "An add of this would be REFUSED until answered "
                    "(--new, --extends ID, --duplicate-of ID, --related ID)"
                )
            else:
                verdict = "An add of this would be filed, with these listed beside it"
            print(verdict + ".")
        return out.exit
    if _asks(out):
        print(out.reason, file=sys.stderr)
        if c.json:
            c.out(out.reason, out.body())
            return out.exit
        if data.get("aborted"):
            return out.exit
        argv = list(getattr(a, "_argv", []))
        print("Answer it by running the same command with one of:", file=sys.stderr)
        for line in commands(argv, data["candidates"]):
            print(f"  {line}", file=sys.stderr)
        print(
            "(`ddflow similar` shows the candidates for a text; --check does it for this add.)",
            file=sys.stderr,
        )
        return out.exit
    if not out.exit and data.get("candidates") and not c.json and not data.get("extended"):
        # Filed anyway (`warn`, or a match below the asking threshold): the add reports
        # itself as usual, after the records it reads like.
        print("It reads like:", file=sys.stderr)
        print("\n".join(candidate_lines(data["candidates"])), file=sys.stderr)
    if data.get("extended") and not out.exit:
        msg = (
            f"added to {data['extended']} ({data['extended_kind']}, {data['relation']}): "
            f"no new record was made"
        )
        c.out(msg, out.body())
        return 0
    return None


def finish(a, c, out: Outcome) -> int | None:
    """`settle`, and -- for an add that failed or was refused outright -- the reason on stderr
    and its exit code. ``None`` when the add went through and the command reports it as before:
    the three lines every add command opened its report with."""
    settled = settle(a, c, out)
    if settled is not None:
        return settled
    if out.exit:
        print(out.reason, file=sys.stderr)
        return out.exit
    return None
