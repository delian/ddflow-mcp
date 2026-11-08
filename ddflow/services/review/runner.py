"""The review scheduler: race chunks, retry, settle, and merge re-reviews.

Split out of ``ddflow/services/review.py``: ``_bounded``/``_race``/``_retry_truncated``
concurrency, ``_preflight``/``_absorb_chunk``, and the ``review``/``_settle``/
``merge_rerun`` top level. It sits above ``.engine`` (chunking + backends) and
``.transport`` (cancellation).
"""

from __future__ import annotations

import dataclasses
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from ...core import blobref as B
from ...core import unidiff
from ...core.digest import content_digest
from .. import prompts as PR
from .engine import (
    DID_NOT_CONVERGE,
    TRUNCATED,
    _chunk_budget,
    _no_answer_in_time,
    chunk_files,
    diff_fence,
    ensure_running,
    parse,
    strip_hunk_context,
)
from .model import (
    ERROR,
    PARTIAL,
    REVIEWED,
    UNAVAILABLE,
    Finding,
    Reviewer,
    ReviewResult,
    classify_exhaustion,
    finding_digest,
)
from .transport import _Cancel, _cancel_on_error, _running


def _preflight(rev: Reviewer, diff: str, res: ReviewResult) -> ReviewResult | None:
    """Everything that makes a review impossible before a single request is sent.

    Returns the finished (UNAVAILABLE) result, or None to proceed. Separated out
    because there are a lot of distinct ways not to review and each needs its own
    remedy in the message -- collapsing them into one "review failed" sends an operator
    hunting through the wrong half of the system.
    """
    if not rev.enabled:
        res.status, res.reason = UNAVAILABLE, "reviewer is disabled in config"
        return res
    if rev.kind == "command":
        if not rev.command:
            res.status, res.reason = UNAVAILABLE, "kind='command' but no command is set"
            return res
    elif not rev.base_url or not rev.model:
        res.status, res.reason = UNAVAILABLE, (f"reviewer {rev.name!r} has no base_url or model")
        return res
    if not diff.strip():
        # An empty diff is the commonest way a review passes vacuously: the command ran,
        # the model replied "looks fine", and nothing was actually examined.
        res.status, res.reason = UNAVAILABLE, "the diff is empty — nothing was reviewed"
        return res

    # Opt-in: start a local server if one is configured and not already answering.
    # Starting a multi-gigabyte model server as a side effect of asking for a code
    # review is a surprise nobody wants by default, so this runs only when the reviewer
    # carries a `launch` block.
    if rev.launch:
        ok, note = ensure_running(rev)
        if not ok:
            res.status, res.reason = UNAVAILABLE, note
            return res
        if note:
            res.reason = note

    return None


#: The reasons `_absorb_chunk` gives a reply that is not a review.
EMPTY_COMPLETION = "empty completion"
OFF_CONTRACT = "came back OFF CONTRACT:"


def _absorb_chunk(
    res: ReviewResult,
    index: int,
    content: str,
    err: str,
    all_raw: list[str],
    files: list[str] | None = None,
    partial: list[str] | None = None,
) -> list[Finding] | None:
    """Fold one chunk's reply into the result. Returns its findings, or None if it did
    not produce a usable review -- recorded in `res.unreviewed` with its files.

    The three not-a-review cases are kept distinct because their remedies differ:
    a transport error (endpoint, key, binary), an EMPTY completion, and an
    OFF-CONTRACT reply — thousands of tokens of deliberation with no STATUS block in
    them. Only the last is easy to mistake for a review, which is why length is never
    treated as a verdict.
    """

    def lost(why: str) -> None:
        res.unreviewed.append({"chunk": index, "files": list(files or []), "reason": why})

    if err:
        lost(err)
        # The parts of a chunk that did come back: their findings are real, the chunk is
        # still unreviewed (and says so), so nothing is claimed that was not looked at.
        for part in partial or ():
            found, _ok = parse(part)
            for f in found:
                f.chunk = index
                f.digest = finding_digest(f.severity, f.location, f.title, f.detail)
            res.findings.extend(found)
            all_raw.append(part)
        if partial:
            res.unreviewed[-1]["partial"] = len(partial)
        return None
    if not content:
        res.chunks_off_contract += 1
        lost(EMPTY_COMPLETION)
        return None
    all_raw.append(content)
    findings, on_contract = parse(content)
    if not on_contract:
        res.chunks_off_contract += 1
        lost(f"{OFF_CONTRACT} {len(content)} chars with no STATUS: block. Not counted as reviewed.")
        return None
    res.chunks_reviewed += 1
    res.reviewed.append(index)
    for f in findings:
        f.chunk = index
        f.digest = finding_digest(f.severity, f.location, f.title, f.detail)
    res.findings.extend(findings)
    return findings


#: The most requests `max_concurrency = 0` puts in flight at once. A server queues what
#: it cannot run, so this bounds only threads and sockets, not what the server carries.
AUTO_CONCURRENCY_CEILING = 32


def _concurrency(rev: Reviewer, requests: int) -> int:
    """Requests in flight for a review of ``requests`` chunks: the cap, or one wave."""
    cap = int(rev.max_concurrency)
    if cap > 0:
        return cap
    return max(1, min(requests * max(1, int(rev.hedge)), AUTO_CONCURRENCY_CEILING))


def _waves(rev: Reviewer, sent: int) -> int:
    """Rounds of FIRST copies a review of `sent` chunks needs at `rev`'s concurrency: `_race`
    queues every chunk's first copy before any hedge copy. A first copy that fails is
    answered by a hedge copy in a later round, which this does not count -- such a review
    then reads as slower per request than it was, never faster."""
    return max(1, -(-sent // _concurrency(rev, sent))) if sent else 1


def _bounded(
    rev: Reviewer, system: str, user: str, bound: float, token: _Cancel, tree: Path | None
) -> tuple[str, str]:
    """One request, cut at ``bound`` seconds by a hard cancel, not by the socket's read timeout
    alone: that one is renewed by every byte a loaded server trickles, so a request could
    outlive the bound it stated (bug B-review-chunk-overran-bound: 332 s past 1800 s). The
    cancel is one second late, so the read timeout still gets the first word."""
    fired = threading.Event()
    timer = threading.Timer(bound + 1.0, lambda: (fired.set(), token.cancel()))
    timer.daemon = True
    _running.cancel = token
    timer.start()
    try:
        # Resolved through the package so a monkeypatch of
        # `ddflow.services.review._chat` (test_prompts) is honoured: a static
        # import would bind engine._chat once at import time.
        _do_chat = sys.modules[__package__]._chat

        got = _do_chat(rev, system, user, bound, tree)
    finally:
        timer.cancel()
        _running.cancel = None
    if fired.is_set() and (got[1] or not got[0]):
        return "", _no_answer_in_time(bound) + " Cancelled at the wall-clock bound."
    return got


def _race(
    rev: Reviewer,
    system: str,
    users: list[str],
    started: float,
    *,
    on_settled: Callable[[int, tuple[str, str]], None] | None = None,
    on_tick: Callable[[list[int]], None] | None = None,
    tick_s: float = 0,
    tree: Path | None = None,
) -> list[tuple[str, str]]:
    """(content, error) for every chunk: chunks in parallel, ``rev.hedge`` copies each.

    Up to ``_concurrency(rev, len(users))`` requests are in flight. Every chunk's first copy is
    queued before any chunk's second, so a narrow cap spends itself on coverage before
    speculation. A chunk is settled by its first copy to come back ON CONTRACT -- the
    others are cancelled (`_Cancel`) and any still queued never start -- or, if every
    copy failed, by its first failure, reported exactly as a sequential review would.

    ``on_settled(i, result)`` is called as each chunk settles, and ``on_tick(waiting)``
    -- with the indices of the chunks still unsettled -- every ``tick_s`` seconds while
    any are in flight. Both on THIS thread: the tick renews the caller's lease, and the
    event log's parse cache is not safe to touch from a worker (roborev 827). A review
    used to say nothing between its first line and its last for half an hour, and an
    agent watching a silent tool kills it (bug B9d8bd466c3); meanwhile the lease it
    held expired (bugs Bc6ec4fd40d, Bf0cccb8fb1).
    """

    if not users:  # `review` refuses an empty diff first; this is for any other caller
        return []
    hedge = max(1, int(rev.hedge))
    n = len(users)
    cap = _concurrency(rev, n)
    tokens = {(i, c): _Cancel() for i in range(n) for c in range(hedge)}
    settled: dict[int, tuple[str, str]] = {}
    first_failure: dict[int, tuple[str, str]] = {}
    left = dict.fromkeys(range(n), hedge)

    def attempt(i: int, c: int) -> tuple[str, str] | None:
        if i in settled or tokens[(i, c)].cancelled:
            return None  # another copy won, or a queued twin was cancelled before it started
        remaining = rev.total_budget_s - (time.time() - started)
        if remaining <= 0:
            return "", f"total budget {rev.total_budget_s}s exhausted before it started"
        return _bounded(rev, system, users[i], min(rev.timeout_s, remaining), tokens[(i, c)], tree)

    order = [(i, c) for c in range(hedge) for i in range(n)]
    with ThreadPoolExecutor(max_workers=min(cap, len(order))) as pool, _cancel_on_error(tokens):
        futures = {pool.submit(attempt, i, c): (i, c) for i, c in order}
        pending = set(futures)
        next_tick = time.time() + tick_s
        while pending:
            timeout = max(0.0, next_tick - time.time()) if on_tick and tick_s > 0 else None
            done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            for fut in done:
                i, c = futures[fut]
                got = fut.result()
                if got is None or i in settled:
                    continue
                left[i] -= 1
                content, err = got
                if not err and content and parse(content)[1]:
                    settled[i] = got
                    _cancel_twins(tokens, i, c, hedge)
                elif _retryable(err):
                    # A chunk lost to its OWN size -- out of tokens, or no answer within the
                    # bound -- is deterministic: a hedge copy is the SAME chunk with the SAME
                    # budget and timeout, so it must fail identically. Cancel its twins instead
                    # of running them: a queued one would otherwise start only after this one
                    # timed out and spend the SAME timeout_s again, so a lost chunk would wait
                    # hedge x timeout_s while the tick claimed "cut at timeout_s"
                    # (B-review-chunk-timeout-not-enforced: chunks were still waiting at 3600s
                    # on a 1800s timeout). `_retry_truncated` then halves it, which is the
                    # sanctioned recovery, not an identical re-request.
                    settled[i] = first_failure.get(i, got)
                    _cancel_twins(tokens, i, c, hedge)
                elif left[i] == 0:
                    settled[i] = first_failure.get(i, got)
                else:
                    first_failure.setdefault(i, got)
                    continue
                if on_settled:
                    on_settled(i, settled[i])
            if on_tick and tick_s > 0 and time.time() >= next_tick:
                next_tick = time.time() + tick_s
                waiting = [i for i in range(n) if i not in settled]
                if waiting:
                    on_tick(waiting)
    return [settled[i] for i in range(n)]


def _cancel_twins(tokens: dict, i: int, c: int, hedge: int) -> None:
    """Cancel every copy of chunk ``i`` but the settled one ``c``; a queued twin then sees
    ``i in settled`` in `attempt` and never starts."""
    for other in range(hedge):
        if other != c:
            tokens[(i, other)].cancel()


def _retryable(err: str) -> bool:
    """A chunk lost to its own size: out of tokens, or no answer within the bound. Smaller
    pieces can fix either; an unreachable endpoint or a bad key they cannot."""
    return err.startswith((TRUNCATED, DID_NOT_CONVERGE))


def _halves(piece: str) -> list[str]:
    """``piece`` in two (by file, then by hunk, as `split_diff` always does), or [] when it
    does not split into parts that together carry every file header: halves missing a file
    would let the chunk count as reviewed without it (split_diff no longer drops a hunkless
    section, B779270c994; this stays as the invariant check, not as a workaround). No header at
    all is not "every header kept": there is nothing to check the halves against (roborev)."""
    _split_diff = sys.modules[__package__].split_diff

    pieces = _split_diff(piece, max(1, (len(piece) + 1) // 2))
    headers = unidiff.headers(piece)
    if len(pieces) <= 1 or not headers or not all(any(h in p for p in pieces) for h in headers):
        return []
    return pieces


def _retry_truncated(
    rev: Reviewer,
    system: str,
    chunks: list[str],
    results: list[tuple[str, str]],
    render,
    started: float,
    *,
    rounds: int = 1,
    partial: dict[int, list[str]] | None = None,
    **race_kw: Any,
) -> list[tuple[str, str]]:
    """Give a chunk that ran out of budget, or of time, on EVERY copy another go in halves.

    A reasoning model's thinking length is random per call and grows with the input,
    so a chunk lost to `TRUNCATED` or `DID NOT CONVERGE` is retried: in halves when it
    splits (by file, then by hunk, as `split_diff` always does), whole -- once, and only
    for a truncation, whose length is a fresh draw -- when it does not. A half that is lost
    again is halved in the next of at most ``max(1, rounds)`` rounds, all inside `total_budget_s`.
    The chunk counts as reviewed only if every part comes back on contract; the parts'
    replies are joined into the chunk's one reply, so chunk counts and reports are
    unchanged. A chunk some parts of which never came back is unreviewed with the reason
    and how many parts did; the replies of the parts that did are put in ``partial[i]`` so
    their findings are kept (bug B587e607ad9: a ~4.5k-char chunk looped for 1800 s twice).
    """
    lost = [i for i, (_c, err) in enumerate(results) if _retryable(err)]
    if not lost:
        return results
    # per chunk: its parts as [piece, content, error]
    parts: dict[int, list[list[str]]] = {i: [[chunks[i], *results[i]]] for i in lost}
    tried: set[int] = set()
    for rnd in range(max(1, rounds)):  # below 1 is read as 1: a chunk lost to size gets one more go
        todo: list[tuple[int, int]] = []
        grown: dict[int, list[list[str]]] = {}
        for i in lost:
            cur: list[list[str]] = []
            for piece, content, err in parts[i]:
                if not _retryable(err):
                    cur.append([piece, content, err])
                    continue
                pieces = _halves(piece) or (
                    [piece] if rnd == 0 and err.startswith(TRUNCATED) else []
                )
                if not pieces:
                    cur.append([piece, content, err])
                    continue
                for sub in pieces:
                    todo.append((i, len(cur)))
                    cur.append([sub, "", err])
            grown[i] = cur
        if not todo:
            break
        parts = grown
        tried.update(i for i, _k in todo)
        users = [render(parts[i][k][0], i + 1) for i, k in todo]
        _race_ = sys.modules[__package__]._race

        again = _race_(rev, system, users, started, **race_kw)
        for (i, k), (content, err) in zip(todo, again, strict=True):
            parts[i][k][1:] = [content, err]
    out = list(results)
    for i in lost:
        mine = parts[i]
        good = [c for _p, c, e in mine if not e and c and parse(c)[1]]
        failed = next(((c, e) for _p, c, e in mine if e or not c or not parse(c)[1]), None)
        if failed is None:
            out[i] = ("\n\n".join(good), "")
            continue
        _c, err = failed
        # An empty or off-contract part has no error of its own: report the original failure.
        why = err or results[i][1]
        if i not in tried:
            note = "not retried: the chunk does not split"
        elif len(mine) > 1 or good:
            note = f"retried in {len(mine)} part(s), {len(good)} reviewed; the rest is unreviewed"
        else:
            note = "retried, still unreviewed"
        out[i] = ("", f"{why} ({note})")
        if partial is not None and good:
            partial[i] = good
    return out


class _Progress:
    """The lines a running review prints, and the tick it hands its caller.

    One line per chunk as it settles, and one naming what is still awaited every tick:
    a review that said nothing for half an hour was killed as hung (bug B9d8bd466c3).
    """

    def __init__(self, files, started, timeout_s, on_progress=None, on_tick=None) -> None:
        self.files, self.started, self.timeout_s = files, started, timeout_s
        self.round_started = started
        self.on_progress, self.on_tick = on_progress, on_tick

    def _say(self, line: str) -> None:
        if self.on_progress:
            self.on_progress(line)

    def _where(self, i: int) -> str:
        names = ", ".join(self.files[i]) or "no file header"
        return f"chunk {i + 1}/{len(self.files)} ({names})"

    def settled(self, i: int, got: tuple[str, str], how: str = "") -> None:
        content, err = got
        found, on_contract = parse(content) if content and not err else ([], False)
        elapsed = f"{time.time() - self.started:.0f}s"
        if on_contract:
            self._say(f"  {self._where(i)}: reviewed{how}, {len(found)} finding(s), {elapsed}")
            return
        why = (err or ("off contract" if content else "empty completion")).splitlines()[0]
        self._say(f"  {self._where(i)}: NOT reviewed{how}, {elapsed} -- {why[:200]}")

    def tick(self, waiting: list[int], how: str = "") -> None:
        if self.on_tick:
            self.on_tick()
        self._say(
            f"  ... waiting on {len(waiting)} of {len(self.files)} chunk(s){how} "
            f"({', '.join(str(i + 1) for i in waiting)}), "
            f"{time.time() - self.started:.0f}s elapsed, {time.time() - self.round_started:.0f}s "
            f"in this round; each request is cut at {self.timeout_s}s"
        )


def _name_unreviewed(res: ReviewResult) -> None:
    """Put EVERY chunk nobody reviewed, with its files, into the reason.

    The author needs to know what lacks coverage, not only the first thing that went
    wrong: "6/9 reviewed -- chunk 5: TRUNCATED" left two missing chunks unnamed and
    every file unnamed (bug Bd2332f8f2a).
    """
    if not res.unreviewed:
        return
    n = res.chunks_total
    lines = [
        f"  chunk {u['chunk']}/{n} ({', '.join(u['files']) or 'no file header'}): {u['reason']}"
        for u in res.unreviewed
    ]
    head = f"{len(res.unreviewed)} of {n} chunk(s) not reviewed:"
    res.reason = "\n".join([*([res.reason] if res.reason else []), head, *lines])


def review(  # noqa: PLR0913 -- one reviewer run: what, how, and four callbacks
    rev: Reviewer,
    diff: str,
    intent: str,
    *,
    context: str = "",
    repo: Path | None = None,
    tree: Path | None = None,
    prompt_overrides: dict[str, str] | None = None,
    only: list[int] | None = None,
    on_progress: Callable[[str], None] | None = None,
    on_tick: Callable[[], None] | None = None,
    tick_s: float = 60,
    on_chunk: Callable[[int, str], None] | None = None,
    manifest: str = "",
    chunk_max_tokens: int = 0,
    split_rounds: int = 2,
) -> ReviewResult:
    """Run one reviewer over one diff. Never raises; encodes everything in the status.

    ``on_progress`` gets a line as each chunk settles and, every ``tick_s`` while chunks
    are in flight, one naming what is still awaited; ``on_tick`` is called at the same
    cadence, on the caller's thread (the api renews the caller's lease there).
    ``on_chunk(n, reply)`` gets each chunk's whole reply the moment it arrives, so a
    caller can keep the finding bodies even if the run is cut short (bug B206).
    ``tree`` is the working tree under review: a command reviewer must leave it as it
    found it, and only it is watched (B708761957b).
    ``manifest`` (``review_manifest.build``) lists the whole change; every chunk's prompt
    carries it, so a reviewer can check what a sibling chunk adds.
    ``only`` names the chunks (1-based, as a full run numbers them) to send; the rest
    are neither sent nor counted as lost -- `merge_rerun` folds such a run into the
    record of the full one.
    ``chunk_max_tokens`` (> 0) caps the tokens one chunk's request may spend below the
    reviewer's own ``max_tokens``; ``split_rounds`` is how many times a chunk lost to size
    (out of tokens or time) is halved and retried (``_retry_truncated``).
    """
    if chunk_max_tokens > 0 and chunk_max_tokens < rev.max_tokens:
        rev = dataclasses.replace(rev, max_tokens=chunk_max_tokens)
    res = ReviewResult(reviewer=rev.name, model=rev.model, family=rev.resolved_family())
    started = time.time()

    problem = _preflight(rev, diff, res)
    if problem is not None:
        return problem

    stripped = strip_hunk_context(diff)
    chunk_budget = _chunk_budget(rev)
    _split_diff = sys.modules[__package__].split_diff

    chunks = _split_diff(stripped, chunk_budget)
    res.chunks_total = len(chunks)
    res.diff_sha, res.max_chunk_chars = diff_digest(stripped), chunk_budget
    files = [chunk_files(c) for c in chunks]
    wanted = sorted(set(only or ())) or list(range(1, len(chunks) + 1))
    outside = [n for n in wanted if not 1 <= n <= len(chunks)]
    if outside:
        res.status = ERROR
        res.reason = f"no chunk {outside} -- this diff has chunks 1..{len(chunks)}"
        return res
    res.requested = sorted(set(only or ()))

    overrides = dict(prompt_overrides or {})
    if rev.system_prompt_path:
        # A per-reviewer override still wins: two reviewers may want different
        # instructions, which a single project-wide template cannot express.
        overrides["review_system"] = rev.system_prompt_path
    try:
        system = PR.render(
            PR.resolve("review_system", repo, overrides), extra_rules=rev.extra_rules.strip()
        )
        user_tmpl = PR.resolve("review_user", repo, overrides)
    except PR.TemplateError as exc:
        res.status, res.reason = ERROR, str(exc)
        return res

    def render(chunk: str, i: int) -> str:
        # ONE renderer for the first pass and the truncation retry, so they cannot drift.
        return PR.render(
            user_tmpl,
            intent=intent,
            context=context,
            diff=chunk,
            fence=diff_fence(chunk),
            chunk_index=i,
            chunk_total=len(chunks),
            manifest=manifest,
            manifest_fence=diff_fence(manifest),
            chunk_files=", ".join(chunk_files(chunk)),
        )

    try:
        users = [render(chunks[n - 1], n) for n in wanted]
    except PR.TemplateError as exc:
        res.status, res.reason = ERROR, f"review_user template: {exc}"
        return res

    progress = _Progress(files, started, rev.timeout_s, on_progress, on_tick)

    def _keep(n: int, got: tuple[str, str]) -> None:
        if on_chunk and got[0]:
            on_chunk(n, got[0])

    # `_race` and the retry index the SENT chunks (positions in `wanted`); progress and
    # absorption speak chunk numbers.
    res.waves = _waves(rev, len(users))
    _race_ = sys.modules[__package__]._race

    first = _race_(
        rev,
        system,
        users,
        started,
        on_settled=lambda k, got: (_keep(wanted[k], got), progress.settled(wanted[k] - 1, got)),
        on_tick=lambda waiting: progress.tick([wanted[k] - 1 for k in waiting]),
        tick_s=tick_s,
        tree=tree,
    )
    # The retry races PARTS of the lost chunks, so its indices are not chunk numbers:
    # its ticks name the chunks being retried instead (critic).
    retried = [wanted[k] - 1 for k, (_c, err) in enumerate(first) if _retryable(err)]
    progress.round_started = time.time()
    partial: dict[int, list[str]] = {}
    results = _retry_truncated(
        rev,
        system,
        [chunks[n - 1] for n in wanted],
        first,
        lambda chunk, k: render(chunk, wanted[k - 1]),
        started,
        on_tick=lambda _parts: progress.tick(retried, " on retry"),
        tree=tree,
        tick_s=tick_s,
        rounds=split_rounds,
        partial=partial,
    )
    for k, (before, after) in enumerate(zip(first, results, strict=True)):
        if before != after:
            _keep(wanted[k], after)
            progress.settled(wanted[k] - 1, after, " on retry")

    all_raw: list[str] = []
    # Absorbed in CHUNK order whatever order they finished in, so the findings, the raw
    # transcript and the reported failures do not depend on which copy was quick.
    for n, (content, err) in zip(wanted, results, strict=True):
        _absorb_chunk(res, n, content, err, all_raw, files[n - 1], partial.get(wanted.index(n)))
    res.raw = "\n\n---\n\n".join(all_raw)
    res.elapsed_s = time.time() - started
    _settle(res)
    _stamp_raised_by(res, res.findings)
    return res


def _settle(res: ReviewResult) -> None:
    """Status and reason from coverage: every chunk, some, or none."""
    _name_unreviewed(res)
    res.degraded = classify_exhaustion(res.reason) if res.reason else ""
    if res.chunks_reviewed == 0:
        res.status = UNAVAILABLE
        res.reason = res.reason or "no chunk produced a usable review"
    elif res.chunks_reviewed < res.chunks_total:
        res.status = PARTIAL
        if not res.unreviewed:  # a --chunk run with nothing to merge into
            res.reason = f"only chunk(s) {res.requested} were sent; the rest were not reviewed here"
    else:
        res.status = REVIEWED
        res.reason = ""


def diff_digest(diff: str) -> str:
    """What a chunk number refers to: the diff as reviewers are sent it."""

    return content_digest(strip_hunk_context(diff), errors="replace")


def _in_rerun_cut(prior: dict[str, Any], f: dict[str, Any]) -> bool:
    """Is finding ``f`` of the record ``prior`` numbered in the cut a re-run chunk refers to?
    In a record of a DELTA round only the delta's own findings (`delta_raised`) are; the ones
    it carried from an earlier round are numbered in that round's cut, and a chunk number of
    this one must not drop them (B00e6b5a540). Any other record numbers all in one cut."""
    if "delta_raised" not in prior:
        return True
    digest = f.get("digest")
    # one the delta raised again is the EARLIER copy that stayed, numbered in the earlier cut
    return digest in set(prior["delta_raised"]) and digest not in set(
        prior.get("delta_reraised", ())
    )


def _kept_over_rerun(prior: dict[str, Any], f: dict[str, Any], again: set[int], who: str) -> bool:
    """Should finding ``f`` of the record ``prior`` survive a re-run of chunks ``again`` with
    reviewer ``who``? A chunk that is not re-run stays; a finding numbered in an earlier cut
    (a delta carried it) stays too (`_in_rerun_cut`); and a RE-RUN chunk's finding is replaced
    only when the SAME reviewer raised it. A peer reviewer's finding of that chunk is the other
    reviewer's verdict, kept -- dropping it would be a false pass route (bug
    B-chunk-rerun-drops-peer-findings). A finding that names NO reviewer (an old record) is
    kept too: it cannot be attributed, and dropping an unattributed finding is the same false
    pass."""
    if int(f.get("chunk", 0)) not in again:
        return True
    if not _in_rerun_cut(prior, f):
        return True
    raiser = f.get("reviewer", "")
    if not raiser:
        return True
    return raiser != who


def _stamp_raised_by(res: ReviewResult, findings: list[Finding]) -> None:
    """Name the reviewer on the findings IT raised. The absorbed ``Finding`` carries none, so
    this is where its ``reviewer``/``family`` are set. A finding ``merge_rerun`` carried from
    an earlier record already names its reviewer (or none, an old record: kept unattributed),
    and must NOT be relabelled here."""
    family = (res.family or "").strip().lower()
    for f in findings:
        if not f.reviewer:
            f.reviewer = res.reviewer
            f.family = family


def merge_rerun(prior: dict[str, Any], res: ReviewResult) -> str:
    """Fold a `--chunk` re-review into the evidence of the review it re-runs part of.

    Returns why it cannot ("" when merged). Only into a record of the SAME cut -- the
    same diff, chunk size and reviewer -- because a chunk number means nothing across
    two cuts (bug Bd2332f8f2a: a deterministically truncated chunk forced a 25-40 min
    full re-run, or an out-of-band model call ddflow could not verify). The re-run
    chunks' outcomes replace theirs; every other chunk keeps what it had.
    """
    for key, mine in (
        ("reviewer", res.reviewer),
        ("diff_sha", res.diff_sha),
        ("max_chunk_chars", res.max_chunk_chars),
    ):
        if prior.get(key) != mine:
            return (
                f"the recorded review differs in {key} ({prior.get(key)!r} vs {mine!r}), "
                f"so its chunk numbers are not this diff's: run the full review"
            )
    if "reviewed" not in prior:
        return "the recorded review predates per-chunk evidence: run the full review"
    if isinstance(prior.get("chunk_findings"), B.Unavailable):
        return f"the recorded review's findings are {prior['chunk_findings']}: run the full review"
    again = set(res.requested)
    kept = {int(n) for n in prior["reviewed"]} - again
    res.reviewed = sorted(kept | set(res.reviewed))
    res.chunks_reviewed = len(res.reviewed)
    own = res.findings
    _stamp_raised_by(res, own)
    res.findings = sorted(
        [
            Finding(
                severity=f.get("severity", ""),
                title=f.get("title", ""),
                detail=f.get("detail", ""),
                location=f.get("location", ""),
                chunk=int(f.get("chunk", 0)),
                digest=f.get("digest", ""),
                reviewer=f.get("reviewer", ""),
                family=f.get("family", ""),
            )
            for f in prior.get("chunk_findings", [])
            if _kept_over_rerun(prior, f, again, res.reviewer)
        ]
        + own,
        key=lambda f: f.chunk,
    )
    res.unreviewed = sorted(
        [u for u in prior.get("unreviewed", []) if int(u.get("chunk", 0)) not in again | kept]
        + res.unreviewed,
        key=lambda u: u["chunk"],
    )
    # Off-contract chunks are among the unreviewed, by the reason `_absorb_chunk` gives
    # them; counted again so the merged coverage does not drop the earlier ones.
    res.chunks_off_contract = sum(
        1
        for u in res.unreviewed
        if u["reason"] == EMPTY_COMPLETION or u["reason"].startswith(OFF_CONTRACT)
    )
    res.reason = ""
    _settle(res)
    return ""
