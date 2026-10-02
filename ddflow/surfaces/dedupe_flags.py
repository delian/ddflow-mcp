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


def add_flags(parser) -> None:
    """The answer flags and ``--check`` on one add command's parser."""
    g = parser.add_mutually_exclusive_group()
    g.add_argument(
        "--new",
        action="store_true",
        help="answer a possible-duplicate refusal: this is a different record, file it",
    )
    g.add_argument(
        "--extends",
        metavar="ID",
        default="",
        help="answer it: this adds to record ID -- appended to ID while it is open and "
        "unclaimed, else filed as a new record linked to it",
    )
    g.add_argument(
        "--duplicate-of",
        metavar="ID",
        default="",
        help="answer it: this is the same thing as record ID (handled like --extends)",
    )
    g.add_argument(
        "--related",
        metavar="ID",
        default="",
        help="answer it: a different record that is related to ID; linked both ways",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="dry run: print the existing records this would be refused as a duplicate "
        "of and write nothing (exit 0 with candidates, 2 with none)",
    )


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


def candidate_lines(rows: list[dict[str, Any]], *, numbered: bool = False) -> list[str]:
    lines = []
    for i, r in enumerate(rows, 1):
        flags = f"  [{', '.join(r['flags'])}]" if r.get("flags") else ""
        lead = f"{i}. " if numbered else ""
        lines.append(
            f"{lead}{r['id']}  {r['kind']}  {r['score']:.2f}  {r['state']}{flags}\n    {r['title']}"
        )
        if r.get("shared"):
            lines[-1] += f"\n    shares: {', '.join(r['shared'])}"
    return lines


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
    if ref.isdigit() and 1 <= int(ref) <= len(rows):
        return Answer(rel, rows[int(ref) - 1]["id"])
    ids = {r["id"].lower(): r["id"] for r in rows}
    if ref in ids:
        return Answer(rel, ids[ref])
    return False


def commands(argv: list[str], rows: list[dict[str, Any]]) -> list[str]:
    """The invocation again with each answer appended: ready to copy."""
    base = "ddflow " + shlex.join(argv)
    top = rows[0]["id"]
    return [
        f"{base} --new",
        f"{base} --extends {top}",
        f"{base} --duplicate-of {top}",
        f"{base} --related {top}",
    ]


def settle(a, c, out: Outcome) -> int | None:
    """Print what the check decided and return the exit code; ``None`` when the add
    went its ordinary way and the command should report it as before."""
    data = out.data
    if data.get("check_only"):
        if c.json:
            c.out(out.reason, out.body())
        elif out.exit:
            print(out.reason)
        else:
            print("\n".join(candidate_lines(data["candidates"])))
            print(
                "An add of this would be "
                + (
                    "REFUSED until answered (--new, --extends ID, --duplicate-of ID, --related ID)"
                    if data["would_ask"]
                    else "filed, with these listed beside it"
                )
                + "."
            )
        return out.exit
    if _asks(out):
        if c.json:
            c.out(out.reason, out.body())
            return out.exit
        print(out.reason, file=sys.stderr)
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
    if data.get("extended") and not out.exit:
        msg = (
            f"added to {data['extended']} ({data['extended_kind']}, {data['relation']}): "
            f"no new record was made"
        )
        c.out(msg, out.body())
        return 0
    return None
