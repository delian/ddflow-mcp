"""Diff chunking, verdict parsing, the reviewer backends, and local-server launch.

Split out of ``ddflow/services/review.py``: everything that shapes a chunk, calls a
reviewer endpoint or command, and parses its verdict; the ``review`` scheduler in
``.runner`` sits above this.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import time as _time
from pathlib import Path

from ...adapters.http import HttpProtocolError, HttpStatusError
from ...adapters.reviewer_clients import (
    _COLD,
    CLIENTS,
    DID_NOT_CONVERGE,
    TRUNCATED,
    ReviewerChat,
    temperature_field,
    truncated_reason,
)
from ...adapters.reviewer_clients import (
    cut_off as _cut_off,
)
from ...adapters.reviewer_clients import (
    no_answer_in_time as _no_answer_in_time,
)
from ...core import unidiff
from ...infra import proc as P
from ...infra.container import rewrite_localhost
from .. import cmdrunner as CR
from .. import review_keys
from ..cmdrunner import Declared
from ..gates.reviewers import git_state, git_state_change
from .model import Finding, Reviewer
from .transport import _abort, _attach_to_running

#: Re-exported constants moved to the adapter module: kept importable here so the split
#: package's lazy re-export resolves them exactly as it did when they were defined here.
__all__ = ["DID_NOT_CONVERGE", "TRUNCATED", "_COLD"]

_RUNNER = CR.CommandRunner()

#: Kept ONLY as a last-resort fallback for a stripped deployment where the packaged
#: templates are unreadable. The canonical text lives in
#: `ddflow/templates/prompts/review_system.md` and is what operators edit.
_SYSTEM_FALLBACK = (
    "Review this diff and REFUTE it. Report only defects, never style. If uncertain, "
    "report nothing. End with 'STATUS: FINDINGS <n>' or 'STATUS: NO FINDINGS'."
)


_STATUS_RE = re.compile(r"^STATUS:\s*(NO FINDINGS|FINDINGS\s+(\d+))\s*$", re.M | re.I)
# The location group is `[^\n]*`, NOT `.*`: under re.S the dot crosses newlines, so the
# location swallowed the whole finding body -- every finding rendered as one run-on blob
# with an empty detail, and a multi-finding reply parsed as a single finding.
_FINDING_RE = re.compile(
    r"^FINDING[ \t]+(HIGH|MEDIUM|LOW)[ \t]*([^\n]*)\n(.*?)(?=^FINDING[ \t]|^STATUS:|\Z)",
    re.M | re.I | re.S,
)

#: A citation is ONE token -- a ``path``, ``path:line``, ``path:line:col``,
#: ``path:line-line`` or ``path::symbol`` (nested ``::`` included) -- so it has no
#: whitespace. A reviewer writing a SENTENCE in the location field writes whitespace too,
#: and a ``?`` or ellipsis together with that whitespace is what shows it thinking aloud,
#: not citing one spot (B-review-finding-is-unfinished-reasoning). A bare ``path:257?`` is
#: uncertainty about the line (kept), ``foo.py:10 bar.py:20`` is two cited spots (kept),
#: and ``a.py:12; b.py:34`` is two cited spots separated by a semicolon (kept): only a
#: whitespace-separated run that also carries ``?``/ellipsis is the reviewer thinking. A
#: semicolon is a separator, so it is NOT a prose mark.
_PROSE_MARK = re.compile(r"[?\u2026]|\.\.\.")


def _malformed_reason(location: str) -> str:
    """Why a finding is reasoning rather than a defect, or "" when it is a real finding.

    One deterministic signal (B-review-finding-is-unfinished-reasoning): a location that is
    not a ``path:line``/``path:symbol`` citation -- one carrying whitespace together with a
    ``?`` or ellipsis -- records but does not count.

    The bug also names "the text is reasoning with no failure stated". That half is
    deliberately left alone: whether a paragraph states a defect is semantic, and a word
    list or punctuation test drops genuine findings alongside meta-talk -- the review of
    this very code showed the same classifier drop a real defect with no listed word and no
    full stop and keep meta-talk that ended in a full stop -- so no paragraph test acts, and
    a finding that names a real spot is never silently dropped (fail-closed).
    """
    if location and re.search(r"\s", location) and _PROSE_MARK.search(location):
        return "location is prose, not a path:line or path:symbol"
    return ""


def diff_fence(diff: str) -> str:
    """The backtick fence that holds ``diff`` in the reviewer's prompt: one longer than
    any backtick run inside it, and at least three.

    A fixed ``` closed on the first ``` in the diff, and everything after it -- a line
    such as `STATUS: NO FINDINGS`, which the independence gate reads as the verdict --
    then read as the prompt's own text rather than the changed code.
    """
    longest = max((len(m.group()) for m in re.finditer(r"`+", diff)), default=0)
    return "`" * max(3, longest + 1)


def split_diff(diff: str, max_chars: int) -> list[str]:
    """Chunk a diff, **splitting by file first**.

    Hunk-splitting a multi-file diff produces parts missing their ``diff --git``
    preamble, which is a silently wrong diff rather than a smaller one -- the reviewer
    then reasons about a change to a file it cannot name. Only within one file's diff,
    and only when that single file exceeds the budget, do we split by hunk.
    """
    if len(diff) <= max_chars:
        return [diff] if diff.strip() else []
    per_file = re.split(r"(?m)^(?=diff --git )", diff)
    per_file = [f for f in per_file if f.strip()]

    pieces: list[str] = []
    for f in per_file:
        if len(f) <= max_chars:
            pieces.append(f)
            continue
        # The header keeps its trailing newline. Cut before it, the rest began with a
        # bare "\n" that the split below returned as a piece of its own; `current` then
        # differed from `header`, and the header went out ALONE as a chunk, its hunks
        # in the next -- reviewers reported a new file with no content (B779270c994).
        cut = f.find("\n@@ ")
        header = f if cut < 0 else f[: cut + 1]
        hunks = [h for h in re.split(r"(?m)^(?=@@ )", f[len(header) :]) if h]
        current = header
        for h in hunks:
            if current != header and len(current) + len(h) > max_chars:
                pieces.append(current)
                current = header
            current += h
        # Always: with no hunk at all (a large rename or mode change) the section is
        # sent whole -- it used to be dropped, unreviewed and unreported.
        pieces.append(current)

    # Pack small pieces back up, so request count follows diff SIZE rather than file
    # count -- a change touching forty tiny files should not be forty requests.
    packed: list[str] = []
    for piece in pieces:
        if packed and len(packed[-1]) + len(piece) <= max_chars:
            packed[-1] += piece
        else:
            packed.append(piece)
    return packed


def _chunk_budget(rev: Reviewer) -> int:
    """The largest chunk ``rev`` can reason about within its declared output cap.

    ``max_chunk_chars`` is a bound picked for review QUALITY, not a promise the model
    answers a chunk that size: a reasoning model spends its ``max_tokens`` OUTPUT budget
    thinking before it emits the verdict, and the thinking grows with the input. Measured
    on Qwen3.8-Flash-Next (``Reviewer.max_tokens``): a 30 KB chunk needed 28 381 reasoning
    tokens -- about one reasoning token per input character -- so a chunk near
    ``max_chunk_chars`` can exhaust ``max_tokens`` mid-thought and come back ``TRUNCATED``,
    which ddflow records UNAVAILABLE and a review round is spent (bug B9a2279db22: the qwen
    reviewer hit its 64k-token reasoning cap on ledger-sized chunks). The chunk is capped at
    the output cap, so a reviewer whose ``max_tokens`` is below ``max_chunk_chars`` has its
    chunk split to fit before it is ever sent, and a chunk can never outgrow the budget the
    model has to reason about it.
    """
    return min(rev.max_chunk_chars, rev.max_tokens)


def effective_chunk_budget(rev: Reviewer, chunk_max_tokens: int = 0) -> int:
    """The budget ``review()`` sends after ``chunk_max_tokens`` lowers the request window.

    `_rerun_scope` must compare a recorded cut against the SAME number the reviewing run
    computed: the reporter's own reduction of its output window is part of that cut, or a
    ``--chunk`` re-review of a run under ``[review].chunk_max_tokens`` is falsely refused as
    a changed cut.
    """
    if chunk_max_tokens > 0 and chunk_max_tokens < rev.max_tokens:
        rev = dataclasses.replace(rev, max_tokens=chunk_max_tokens)
    return _chunk_budget(rev)


#: A hunk header and whatever git appended after its closing ``@@``.
_HUNK_CONTEXT_RE = re.compile(r"(?m)^(@{2,} [-+0-9, ]+ @{2,})[ \t][^\n]*$")


def strip_hunk_context(diff: str) -> str:
    """Drop the text git appends after a hunk header's closing ``@@``.

    It is git's funcname GUESS -- the nearest earlier line that looks like a heading --
    and for a config file that is routinely a line from the PREVIOUS block: a TOML hunk
    headed ``@@ -104,11 +106,12 @@ default = true`` above a section whose own line reads
    ``default = false``. Reviewers read it as part of the hunk and reported the wrong
    value as the change's, three rounds running (bug Bbf41d8f07f). The line numbers stay.
    """
    return _HUNK_CONTEXT_RE.sub(r"\1", diff)


def chunk_files(chunk: str) -> list[str]:
    """The files a chunk's diff touches, in order, each once."""
    return list(dict.fromkeys(f.path for f in unidiff.files(chunk)))


def _chat(
    rev: Reviewer, system: str, user: str, timeout_s: float, tree: Path | None = None
) -> tuple[str, str]:
    """One completion, whatever the backend. Returns (content, error). Never raises.

    Four backends, because "any LLM" means four wire formats in practice: the
    OpenAI-compatible one that most providers and every local server speak, Anthropic's
    Messages API, Google's generateContent, and -- the universal fallback -- an
    arbitrary command reading stdin. The last one is what makes a model with no HTTP
    API at all usable as a reviewer. The three wire protocols are `ReviewerClient`
    adapters (``ddflow.adapters.reviewer_clients``), one per kind, so a new kind is a
    new adapter, not another branch here.
    """
    if rev.kind == "command":
        return _chat_command(rev, system, user, timeout_s, tree)
    if rev.kind not in CLIENTS:
        return "", (
            f"unknown reviewer kind {rev.kind!r}; expected one of "
            f"openai, anthropic, gemini, command"
        )
    return _chat_kind(CLIENTS[rev.kind], rev, system, user, timeout_s)


def _chat_kind(client, rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    """Run one wire-protocol `client` (a `ReviewerClient`) for ``rev``.

    The key is resolved HERE (the review service owns key loading), into `ReviewerChat`,
    and the HTTP plumbing is the package's swappable `_post_json` -- so a test that
    monkeypatches ``R._post_json`` or ``R._client`` binds what the adapter calls, exactly
    as it bound ``_chat_openai`` before the wire bodies moved behind the port.
    """
    return client.complete(_reviewer_chat(client, rev, system, user, timeout_s))


def _reviewer_chat(client, rev: Reviewer, system: str, user: str, timeout_s: float):
    """The `ReviewerChat` a wire client ``client`` runs: ``rev``'s request, its resolved key,
    and the review-service glue the adapter layer may not import."""
    got = rev.key_result(getattr(client, "key_env", ""))
    return ReviewerChat(
        system=system,
        user=user,
        timeout_s=timeout_s,
        model=rev.model,
        base_url=rev.base_url,
        max_tokens=rev.max_tokens,
        temperature=rev.temperature,
        extra_body=dict(rev.extra_body),
        api_key=got.value,
        no_key_reason=got.reason,
        post_json=sys.modules[__package__]._post_json,
        has_verdict=(lambda text: parse(text)[1]),
        scrub=(lambda text: review_keys.scrub(text, got.value)),
    )


def _tool_moved(before: dict[str, str] | None, tree: Path | None) -> str:
    """Did a command reviewer, now finished or killed, leave git changed? (B5ce30dd94d)"""
    return git_state_change(before, git_state(tree or os.getcwd()))


def _chat_command(
    rev: Reviewer, system: str, user: str, timeout_s: float, tree: Path | None = None
) -> tuple[str, str]:
    """Run a CLI, prompt on stdin, reply on stdout.

    Deliberately dumb and therefore universal. A non-zero exit is an error (the review
    did not happen), and empty stdout is an error even on exit 0 -- a tool that printed
    nothing reviewed nothing, and treating that as "no findings" is the vacuous pass.
    """
    cmd = rev.command.replace("{model}", rev.model)
    if not cmd.strip():
        return "", "kind='command' but no `command` is configured"
    # The installed-check and the process-group start are `CommandRunner.start`'s, not a
    # second implementation of them (the copy that was here once mis-read `FOO=bar claude`
    # as a missing `FOO=bar`, had no builtin allowlist and let an unbalanced quote escape a
    # function whose contract is to turn every way of not-reviewing into a reported one).
    env = {**os.environ, **rev.env}
    # A reviewer must leave the tree under review as it found it (B5ce30dd94d). That is
    # `tree` -- not this process's cwd, which may be any checkout where a sibling lane
    # commits (B708761957b); with none given, the cwd; the tool runs in the tree it is watched in.
    state_before = git_state(tree or os.getcwd())
    started = _RUNNER.start(
        Declared(cmd, f"reviewer {rev.name!r} command"),
        env=env,
        cwd=tree,
        stdin=P.PIPE,
    )
    if started.unavailable is not None:
        run = started.unavailable
        if run.kind == CR.MISSING:
            return "", (
                f"executable {run.missing!r} is not on PATH -- the reviewer could not run. "
                f"This is NOT a clean review."
            )
        return "", run.reason
    p = started.proc
    _attach_to_running(p)
    try:
        stdout, stderr = p.communicate(f"{system}\n\n{user}", timeout=timeout_s)
    except P.TimeoutExpired:
        _abort(p)
        p.communicate()
        return "", _tool_moved(state_before, tree) or (
            f"{DID_NOT_CONVERGE} command timed out after {timeout_s:.0f}s"
        )
    except OSError as exc:
        _abort(p)
        return "", _tool_moved(state_before, tree) or f"could not execute: {exc}"
    moved = _tool_moved(state_before, tree)
    if moved:
        return "", moved
    out = (stdout or "").strip()
    if p.returncode != 0:
        return "", (f"command exited {p.returncode}: {((stderr or '') + out).strip()[:300]}")
    if not out:
        return "", "command produced no output on stdout"
    return out, ""


def _post(url: str, body: bytes, headers: dict, timeout_s: float):
    """One POST through the package's HTTP client (`_client`, swappable by a test), its
    socket handed to the running hedged copy so it can be cancelled."""
    pkg = sys.modules[__package__]
    return pkg._client().request(
        "POST", url, headers=headers, body=body, timeout=timeout_s, on_connect=_attach_to_running
    )


def _post_json(url: str, payload: dict, headers: dict, timeout_s: float) -> tuple[dict | None, str]:
    """POST JSON, returning (body, error). Never raises.

    The container rewrite belongs HERE, not at the call sites. Three of the four places
    that reach the network applied it and this one did not — and this one is the only
    path `kind="anthropic"` and `kind="gemini"` use. So inside a container an
    openai-kind reviewer on `127.0.0.1` was rewritten to the host and worked, while an
    anthropic- or gemini-kind reviewer pointed at a local gateway (litellm, LM Studio,
    an in-house proxy — all ordinary setups) reported UNAVAILABLE. Container support
    silently covered two thirds of the backends.
    """

    data = json.dumps(payload).encode(
        "utf-8"
    )  # a payload that cannot be encoded is the caller's bug
    try:
        resp = _post(
            rewrite_localhost(url), data, {"Content-Type": "application/json", **headers}, timeout_s
        )
        return json.loads(resp.body.decode("utf-8", "replace")), ""
    except HttpStatusError as exc:
        return None, f"HTTP {exc.status}: {exc.body[:300].decode('utf-8', 'replace')}"
    except TimeoutError:
        return None, _no_answer_in_time(timeout_s)
    except HttpProtocolError as exc:
        return None, _cut_off(exc)
    except OSError as exc:
        return None, f"unreachable: {exc}"
    except (ValueError, json.JSONDecodeError) as exc:
        return None, f"bad response body: {exc}"


def _temperature(rev: Reviewer) -> dict[str, float]:
    """The request's temperature field (the `temperature_field` helper over ``rev``)."""
    return temperature_field(rev.temperature)


def _truncated(rev: Reviewer, *, reasoning_tokens: object = None) -> str:
    """The one budget-exhaustion message (the `truncated_reason` helper over ``rev``)."""
    return truncated_reason(rev.max_tokens, rev.temperature, reasoning_tokens)


def _chat_anthropic(rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    """Anthropic Messages API, through its `ReviewerClient`."""
    return _chat_kind(CLIENTS["anthropic"], rev, system, user, timeout_s)


def _chat_gemini(rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    """Google generateContent, through its `ReviewerClient`."""
    return _chat_kind(CLIENTS["gemini"], rev, system, user, timeout_s)


def _chat_openai(rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    """The OpenAI-compatible shape, through its `ReviewerClient`."""
    return _chat_kind(CLIENTS["openai"], rev, system, user, timeout_s)


def parse(text: str) -> tuple[list[Finding], bool]:
    """(findings, on_contract). Off contract means no STATUS: block at all."""
    status = _STATUS_RE.search(text or "")
    findings = []
    for m in _FINDING_RE.finditer(text or ""):
        location = (m.group(2) or "").strip()
        #: The finding's text is the reviewer's paragraph. A reviewer may write only a
        #: location -- `FINDING MEDIUM y.py:1` with no paragraph -- and that still names
        #: the spot the author must answer, so its text is derived from the location and
        #: a location-only finding is never recorded empty (B-review-finding-empty-text).
        detail = (m.group(3) or "").strip() or location
        title = location or " ".join(detail.split())[:80]
        if not detail:
            #: The reviewer declared a finding but named no location and no paragraph.
            #: It is still a finding -- recorded with a marker, not dropped, so the gate
            #: stays failed (fail-closed) instead of reading a finding as a clean review.
            title = "(unnamed finding)"
            detail = "(reviewer raised a finding but named nothing)"
        findings.append(
            Finding(
                severity=m.group(1).upper(),
                location=location,
                title=title,
                detail=detail,
                malformed=_malformed_reason(location),
            )
        )
    return findings, status is not None


def ensure_running(rev: Reviewer, *, on_log=None) -> tuple[bool, str]:
    """Start a local server if one is configured and is not already answering.

    Returns ``(available, note)``. Never raises, and never leaves a half-started process
    behind: if the readiness probe never passes, the child is terminated and the reason
    is returned. A reviewer stuck "starting" forever is indistinguishable from one that
    is down, except that it also holds a process.
    """

    if rev.kind == "command":
        return True, ""
    spec = rev.launch_spec()
    probe_url = spec.ready_url or (rev.base_url.rstrip("/") + "/models")
    if _url_ok(probe_url):
        return True, ""
    if not spec.command:
        return False, (
            f"{rev.base_url} is not answering, and reviewer {rev.name!r} has no launch "
            f"command. Start the server yourself, or add one:\n"
            f'  launch = {{ command = "ollama serve" }}'
        )

    cmd = spec.command.replace("{model}", rev.model)
    if on_log:
        on_log(f"starting {rev.name}: {cmd}")
    started = _RUNNER.start(
        Declared(cmd, f"reviewer {rev.name!r} launch command"),
        stdout=P.DEVNULL,
        stderr=P.DEVNULL,
        check_installed=False,
    )
    if started.unavailable is not None:
        return (
            False,
            f"could not start {cmd!r}: {started.unavailable.reason.removeprefix('could not execute: ')}",
        )
    proc = started.proc

    deadline = _time.monotonic() + spec.ready_timeout_s
    while _time.monotonic() < deadline:
        if proc.poll() is not None:
            return False, (
                f"the launch command {cmd!r} exited immediately with {proc.returncode}; "
                f"{rev.base_url} is still not answering"
            )
        if _url_ok(probe_url):
            return True, f"started {rev.name} via {cmd!r}"
        _time.sleep(1.0)
    proc.terminate()
    return False, (
        f"{cmd!r} did not become ready at {probe_url} within "
        f"{spec.ready_timeout_s}s; the process was terminated"
    )


#: A server answering 401/404 still IS a server: the question `_url_ok` asks is
#: "is something listening and reachable", not "did the request succeed".
_HTTP_OK_FLOOR, _HTTP_SERVER_ERROR = 200, 500


def _url_ok(url: str, timeout_s: float = 3.0) -> bool:
    """Is something listening and answering there?

    A 401 or 404 counts: it means a server exists and is reachable, which is the
    question. Only a connection error or a 5xx means "not there".
    """

    try:
        pkg = sys.modules[__package__]
        r = pkg._client().request(
            "GET",
            rewrite_localhost(url),
            timeout=timeout_s,
            raise_for_status=False,
            on_connect=_attach_to_running,
        )
    except OSError:  # HttpError is one; the adapter wraps the rest
        return False
    return _HTTP_OK_FLOOR <= r.status < _HTTP_SERVER_ERROR
