"""Commit-message trailer checks: forbidden keys and the required item trailer."""

from __future__ import annotations

import difflib
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from ...config import Config
from ...core.model import fold
from ...core.outcome import FAIL, NOTHING, OK, Verdict
from ...infra import git as G
from ...infra import proc as P
from ...infra.log import EventLog
from ...infra.store import Store


def _key(line: str) -> str:
    """The `<key>` of a `<key>: value` line, past indentation and comment hashes."""
    return line.split(":", 1)[0].strip().lstrip("#").strip()


def check_forbidden_trailers(message: str, keys: list[str]) -> Verdict:
    """Refuse a message carrying any of `keys` as a `<key>:` line. `[enforce].forbidden_trailers`.

    A LINE scan, deliberately stricter than `check_item_trailer`'s use of git's parser:
    git reads trailers only from the final paragraph, while a forge credits a co-author
    from such a line wherever it sits, and the preference being enforced is about the
    line. Case-insensitive, because git and forges treat trailer keys so. Prose that
    merely names the key (`a Co-author line`) is not a `<key>:` line and passes.
    """
    wanted = {k.strip().lower() for k in keys if k.strip()}
    if not wanted:
        return Verdict(OK, "")
    # A leading `#` does not make it a comment that git drops: `git commit -F` cleans up
    # with `whitespace`, which KEEPS `#` lines, so `#<key>: ...` landed in history
    # verbatim (rubber-duck on B-forbid-trailers). Refusing it in the editor route too,
    # where git would strip it, costs one deleted line.
    found = sorted(
        {_key(ln) for ln in message.splitlines() if ":" in ln and _key(ln).lower() in wanted}
    )
    if not found:
        return Verdict(OK, "")
    return Verdict(
        FAIL,
        (
            f"ddflow: this commit message carries {', '.join(f'`{k}:`' for k in found)}, which "
            f"[enforce].forbidden_trailers refuses.\n\n"
            f"Remove the line and commit again. Git runs this check for every agent and for "
            f"`git commit -F`, the editor and merges alike."
        ),
    )


class QueueUnreadable(Exception):
    """The queue could not be read, so a trailer naming an item could not be checked."""


def queue_ids(repo: Path, cfg: Config) -> set[str]:
    """The id of every item in the queue -- phase or task, any state but removed.

    The queue the pre-commit lease check reads: `repo` is the primary checkout (the CLI
    resolves a linked worktree to it) and the log is its `.ddflow/events`. Answered from
    `.ddflow/index.db` when the index is current -- one query, where a fold re-parses
    every event (~120 ms on a 5k-event log, on every commit) -- and from the log itself
    when it is not; `Store.rebuild` projects exactly the non-removed items, so the two
    answers are the same set (`tests/test_trailer_names_item.py::
    test_the_fresh_index_and_the_fold_agree` holds a removed id refused on both paths).
    A damaged index is a cache miss, not an error.

    Raises `QueueUnreadable` when there is no log to read or reading it fails: an
    unverifiable trailer is "could not run", never a pass.
    """

    log = EventLog(repo, "", log_cfg=cfg.log)
    if not log.dir.is_dir():
        raise QueueUnreadable(f"there is no event log at {log.dir}")
    try:
        store = Store(repo, cfg)
        try:
            if not store.stale(log):
                uri = f"{store.path.resolve().as_uri()}?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=5)) as con:
                    return {row[0] for row in con.execute("select id from items")}
        except sqlite3.Error:
            pass
        state = fold(log.read_all(), strict=False)
    except OSError as exc:
        raise QueueUnreadable(f"reading {log.dir} failed: {exc}") from exc
    return {it.id for it in state.items.values() if not it.removed}


def _near_ids(value: str, ids: set[str], limit: int = 3) -> list[str]:
    """Cheap guesses at the id `value` meant: the same id in another case, ids that
    extend it (`160.D` -> `160.D.4`), the longest id it extends, then a close spelling."""

    low = value.lower()
    same = sorted(i for i in ids if i.lower() == low)
    if same:
        return same[:limit]
    longer = sorted(i for i in ids if i.lower().startswith(low))
    shorter = sorted((i for i in ids if low.startswith(i.lower())), key=len)
    near = longer[:limit] + shorter[-1:]
    return near[:limit] or difflib.get_close_matches(value, sorted(ids), n=limit, cutoff=0.8)


def _trailers(message: str) -> list[tuple[str, str]] | None:
    """`(key, value)` for every trailer git reads in `message`; None if git could not
    say -- which is "could not check", never "no trailer" (the caller exits 2).

    Git's OWN trailer parser, not a line scan. Git reads trailers only from the final
    paragraph, so `Item: X` in the body passed a line scan while
    `git log --format='%(trailers:key=Item)'` -- the reconciliation this exists for --
    found nothing (roborev 827). A check that disagrees with the query it serves
    certifies commits the audit will miss.
    """
    parsed = G.run(".", "interpret-trailers", "--parse", input=message, timeout=P.TIMEOUTS["probe"])
    if not parsed.ok:
        return None
    out = []
    for ln in parsed.out.splitlines():
        key, sep, value = ln.partition(":")
        if sep:
            out.append((key.strip(), value.strip()))
    return out


def _missing_trailer_verdict(accepted: list[str], words: dict[str, list[str]]) -> Verdict:
    """The refusal for a commit carrying none of the accepted item trailers."""
    shown = " or ".join(
        f"`{k}: {'|'.join(words[k.lower()]) if k.lower() in words else '<id>'}`" for k in accepted
    )
    return Verdict(
        FAIL,
        (
            f"ddflow: this commit has no {shown} trailer, and "
            f"[enforce].require_item_trailer is on.\n\n"
            f"Add a final line to the commit message, e.g.:\n"
            f"    {accepted[0]}: {words.get(accepted[0].lower(), ['P1.T3'])[0]}\n\n"
            f"It is what lets an audit match commits to queue items mechanically."
        ),
    )


def _trailer_problems(
    checked: list[tuple[str, str]], words: dict[str, list[str]], ids: Callable[[], set[str]]
) -> tuple[list[str], str, bool]:
    """(invalid-trailer descriptions, why the queue was unreadable or "", whether an id was
    unknown). ``ids()`` is called at most once."""
    bad: list[str] = []
    known: set[str] | None = None
    unreadable = ""
    unknown = False
    for key, value in checked:
        allowed = words.get(key.lower())
        if allowed is not None:
            if value not in allowed:
                bad.append(
                    f"    {key}: {value}\n"
                    f"        `{key}` marks a commit that ships no item and takes only: "
                    f"{', '.join(allowed)}"
                )
            continue
        if not value:
            bad.append(f"    {key}:\n        empty -- it must name an item in the queue")
            continue
        if known is None and not unreadable:
            try:
                known = ids()
            except QueueUnreadable as exc:
                unreadable = str(exc) or type(exc).__name__
        if known is None:
            continue
        if value not in known:
            unknown = True
            near = _near_ids(value, known)
            bad.append(
                f"    {key}: {value}\n        no item in the queue has this id"
                + (f" -- did you mean {' or '.join(near)}?" if near else "")
            )
    return bad, unreadable, unknown


def _trailer_verdict(bad: list[str], unreadable: str, unknown: bool) -> Verdict:
    """The verdict for the problems `_trailer_problems` found."""
    if unreadable:
        why = (
            f"ddflow: could not read the queue, so the item id(s) in this commit's trailers "
            f"could not be checked: {unreadable}. This is not a pass; `ddflow doctor` "
            f"diagnoses the log."
        )
        if not bad:
            return Verdict(NOTHING, why)
        bad.append("\n" + why)
    if not bad:
        return Verdict(OK, "")
    return Verdict(
        FAIL,
        (
            "ddflow: this commit carries an item trailer that is not valid, and "
            "[enforce].require_item_trailer is on:\n\n"
            + "\n".join(bad)
            + "\n\nThe trailer is what lets an audit match commits to queue items, so it must "
            "carry the id of a phase or task that has not been removed (`ddflow show <id>` "
            "checks one)."
            + (
                " A key that marks a commit shipping no item takes words instead of ids: "
                "declare them in [enforce].trailer_waivers."
                if unknown
                else ""
            )
            + "\nFix the trailer and commit again; to fix one on a commit already made, "
            "`git commit --amend`."
        ),
    )


def check_item_trailer(
    message: str,
    keys: list[str],
    *,
    ids: Callable[[], set[str]],
    waivers: dict[str, list[str]] | None = None,
    merging: bool = False,
) -> Verdict:
    """Require one of `keys` as a trailer naming a queue item (`Item: P1.T3`) in the
    commit MESSAGE. `(exit, message)`: 0 passes, 1 refuses, 2 could not check.

    Enabled by ``[enforce].require_item_trailer``; the accepted keys are
    ``[enforce].item_trailer_keys`` (a project that has written `Phase: <id>` for months
    keeps writing it). The trailer is what lets an audit reconcile shipped commits
    against the queue with ``git log --format='%(trailers:key=Item,valueonly)'`` instead
    of parsing prose -- so its value must be the id of an item in the queue, from
    `ids()` (`queue_ids`), called at most once and only when a trailer names an item.
    Any non-empty value used to pass, and `Phase: NOPE.99` reached history where the
    audit could never match it (bug B438d336b24).

    A key in `waivers` (``[enforce].trailer_waivers``) marks a commit that ships NO
    item: it is accepted whether or not `keys` lists it, and takes only its declared
    words (`Phase-ships: none`). Every accepted
    trailer present must be valid: one invalid trailer is refused beside a valid one,
    because the audit reads them all. Keys match case-insensitively, as git's
    `%(trailers:key=...)` does.

    Called by the commit-msg hook with the message being committed -- never with
    `COMMIT_EDITMSG` from pre-commit, which is the previous commit's. A merge commit is
    exempt: it carries the trailers of the commits it merges. A `#Item:` comment line
    does not count, because its key is `#Item`; nor does one anywhere but the final
    paragraph, because git does not read it as a trailer there.
    """
    if merging:
        return Verdict(OK, "")
    if not keys and not waivers:
        keys = ["Item"]  # the default the missing-trailer message names must also match
    canon = {k.lower(): k for k in keys}
    words = {k.lower(): list(v) for k, v in (waivers or {}).items()}
    trailers = _trailers(message)
    if trailers is None:
        return Verdict(
            NOTHING,
            (
                "ddflow: `git interpret-trailers --parse` failed, so this commit's item trailer "
                "could not be checked. This is not a pass; check that `git` runs here."
            ),
        )
    # A key in `waivers` is accepted as well: the waiver map is what DECLARES a key that
    # marks a commit shipping no item, so `Phase-ships: none` satisfies the requirement
    # whether or not the key is also listed in item_trailer_keys. An earlier version
    # required both, and a waiver declared alone sat inert (cross-family reviewers,
    # three rounds).
    spelled = {k.lower(): k for k in (waivers or {})}
    accepted = [*keys, *(k for k in (waivers or {}) if k.lower() not in canon)]
    checked = [
        (canon.get(k.lower()) or spelled[k.lower()], v)
        for k, v in trailers
        if k.lower() in canon or k.lower() in words
    ]
    if not checked:
        return _missing_trailer_verdict(accepted, words)
    bad, unreadable, unknown = _trailer_problems(checked, words, ids)
    return _trailer_verdict(bad, unreadable, unknown)
