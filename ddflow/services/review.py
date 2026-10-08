"""Cross-family reviewers — a configured endpoint, not a prompt for the agent to obey.

The `critic` and `rubber_duck` gates exist because a same-family reviewer shares the
author's blind spots, so its agreement is not independent evidence. But a gate that only
*asks* the agent to go find a different model is a gate the agent grades itself on. This
module makes the reviewer a thing ddflow runs: you declare an OpenAI-compatible
endpoint and its pretraining family in TOML, and `ddflow gate run <item> critic`
actually calls it, parses its verdict, and records the evidence.

Everything here is shaped by one rule: **an unavailable reviewer is never a passed
review.** That sounds obvious and is the single easiest thing in a review pipeline to
get wrong, because the failure is silent — the endpoint is down, nothing is printed,
the pipeline goes green. So the exit vocabulary distinguishes four outcomes and the
caller cannot collapse them by accident:

* ``0`` reviewed — findings, or an explicit "NO FINDINGS".
* ``2`` UNAVAILABLE — disabled, unreachable, empty diff, empty completion.
* ``3`` PARTIAL — some chunks came back, or came back **off contract** (no ``STATUS:``
  block at all). Its own code, so it cannot be mistaken for either of the above.
* ``1`` a usage or git error.

Off-contract is its own case for a measured reason: a reasoning model under the wrong
sampling settings can return thousands of tokens of deliberation with no verdict in it,
and exit 0 with a body. Length is not a verdict. If the ``STATUS:`` block is missing the
review did not happen, whatever the token count says.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import family_for
from ..core import unidiff
from ..core.digest import content_digest
from ..infra import proc as P
from ..services.gates import _missing_executable
from ..services.gates.reviewers import git_state, git_state_change

REVIEWED, ERROR, UNAVAILABLE, PARTIAL = 0, 1, 2, 3

#: Endpoints probed by `ddflow reviewers detect`, with the label each usually means.
#: Ordered by how likely they are to be a local inference server rather than something
#: else on a common port.
WELL_KNOWN_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("http://127.0.0.1:11434/v1", "ollama"),
    ("http://127.0.0.1:8000/v1", "vllm / sglang"),
    ("http://127.0.0.1:1234/v1", "LM Studio"),
    ("http://127.0.0.1:8080/v1", "llama.cpp server"),
    ("http://127.0.0.1:30000/v1", "sglang"),
    ("http://127.0.0.1:5000/v1", "text-generation-webui"),
    ("http://127.0.0.1:8081/v1", "llama.cpp (alt)"),
)


#: The old name for `config.family_for` (the one implementation; `gates.family_of` is the same
#: question asked with a project's `[agent].families`). Callers and tests import it from here.
family_of = family_for


#: Ready-made settings for the providers people actually use. `ddflow reviewers add
#: --preset openai` writes the block; nothing here is required, it just saves an operator
#: looking up a base_url and guessing which env var the key lives in.
PRESETS: dict[str, dict[str, Any]] = {
    # --- SaaS, OpenAI-compatible -----------------------------------------------------
    "openai": {
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-5",
    },
    "deepseek": {
        "kind": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
    },
    "groq": {
        "kind": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
    },
    "together": {
        "kind": "openai",
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
    },
    "fireworks": {
        "kind": "openai",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
    },
    "mistral": {
        "kind": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "model": "mistral-large-latest",
    },
    "openrouter": {
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "xai": {
        "kind": "openai",
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "model": "grok-4",
    },
    # --- SaaS, native wire formats ---------------------------------------------------
    "anthropic": {
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-5",
    },
    "gemini": {
        "kind": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key_env": "GEMINI_API_KEY",
        "model": "gemini-2.5-pro",
    },
    # --- Local servers ---------------------------------------------------------------
    "ollama": {
        "kind": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen3:8b",
        "launch": {"command": "ollama serve", "ready_url": "http://127.0.0.1:11434/v1/models"},
    },
    "llamacpp": {"kind": "openai", "base_url": "http://127.0.0.1:8080/v1"},
    "lmstudio": {"kind": "openai", "base_url": "http://127.0.0.1:1234/v1"},
    "vllm": {"kind": "openai", "base_url": "http://127.0.0.1:8000/v1"},
    # --- Anything else, via its own CLI ----------------------------------------------
    # The universal escape hatch: if a model can be reached by a command that reads a
    # prompt on stdin and writes the reply to stdout, it can be a reviewer here. No SDK,
    # no wire format, no dependency.
    "claude-cli": {
        "kind": "command",
        "command": "claude -p --model {model}",
        "model": "claude-sonnet-5",
        "family": "anthropic",
    },
    "gemini-cli": {
        "kind": "command",
        "command": "gemini -m {model} -p",
        "model": "gemini-2.5-pro",
        "family": "google",
    },
    "codex-cli": {
        "kind": "command",
        "command": "codex exec -m {model} -",
        "model": "gpt-5",
        "family": "openai",
    },
    "llm-cli": {"kind": "command", "command": "llm -m {model}", "family": "unknown"},
    "litellm": {
        "kind": "openai",
        "base_url": "http://127.0.0.1:4000/v1",
        "api_key_env": "LITELLM_API_KEY",
        "launch": {
            "command": "litellm --model {model} --port 4000",
            "ready_url": "http://127.0.0.1:4000/v1/models",
        },
    },
}


@dataclass
class LaunchSpec:
    """How to start a local server that is not running yet.

    Optional by design. An operator who already runs ollama gets nothing from this; an
    operator who does not would otherwise see `UNAVAILABLE: unreachable` and have to go
    find out what to start.
    """

    command: str = ""
    ready_url: str = ""
    ready_timeout_s: int = 120
    stop_after: bool = False


@dataclass
class Reviewer:
    """One configured reviewer: local, remote, SaaS, or an arbitrary command."""

    name: str
    #: openai | anthropic | gemini | command
    kind: str = "openai"
    #: For kind="command": a shell command that reads the prompt on stdin and writes
    #: the reply to stdout. `{model}` is substituted. This is what makes ANY tool usable
    #: as a reviewer -- a vendor CLI, an in-house script, a wrapper around a model with
    #: no HTTP API at all.
    command: str = ""
    launch: dict[str, Any] = field(default_factory=dict)
    base_url: str = ""
    model: str = ""
    family: str = ""
    api_key_env: str = ""
    gates: list[str] = field(default_factory=lambda: ["critic"])
    enabled: bool = True
    #: Unset (the default) sends NO temperature, so the server applies the model's own
    #: recommended sampling (vLLM reads it from the model's generation_config; an API
    #: uses its default). ddflow used to send 0.3, and a reasoning model sampled that
    #: cold loops: lesson Lf3feebf5d3 measured DeepSeek repeating "let me reconsider"
    #: until all 64 000 tokens were gone, on every reasoning_effort tried, where the
    #: vendor's 1.0 reviewed the same diff fully (bugs B568e9def3b, B769704d1dc). Set a
    #: value only to override the model's own.
    temperature: float | None = None
    #: Deliberately large. A REASONING model spends this budget thinking BEFORE it
    #: emits any content, so a budget sized for the answer alone produces
    #: `finish_reason: length` with an EMPTY content field -- a review that silently
    #: did not happen. Measured on Qwen3.8-Flash-Next over a 30 KB diff: 6 000 tokens
    #: yielded 0 chars of content (6 000 reasoning tokens, truncated); 32 000 yielded
    #: 2 396 chars with a valid verdict after 28 381 reasoning tokens.
    max_tokens: int = 32000
    timeout_s: int = 900
    #: Smaller than the model's context on purpose: review QUALITY degrades with chunk
    #: size long before the context limit does, and a reasoning model's thinking time
    #: grows faster than linearly in the input.
    max_chunk_chars: int = 30000
    total_budget_s: int = 3600
    #: Copies of each chunk sent at once; the first that comes back ON CONTRACT is the
    #: chunk's review and the rest are cancelled (connection closed, process killed).
    #: Measured on a reasoning model (research Rfa535c6747): the same 3 KB diff took
    #: 64-391 s across 8 identical calls (6k-42k reasoning tokens), and about one call in
    #: four on a hard diff never answers before `max_tokens`. Racing copies cuts that
    #: tail. The cost is compute up to the moment a loser is cancelled -- free on an idle
    #: local server, billed on a metered API: set 1 there if that matters more than time.
    hedge: int = 2
    #: Requests in flight at once, across chunks and their copies. Chunks used to be sent
    #: one after another, so a review took the SUM of its chunks; in parallel it takes the
    #: slowest. On the measured vLLM server 8 concurrent requests were each 28% slower
    #: with 5.7x the throughput. 0 (the default) sends every chunk's copies in ONE wave,
    #: up to `AUTO_CONCURRENCY_CEILING`: a fixed 4 ran a 5-chunk x hedge-2 review in
    #: waves, 1 301-1 465 s against 1 122 s in one (bug B289bf87e8d). A positive value is
    #: a cap, for a server with few slots whose queued requests would time out waiting.
    max_concurrency: int = 0
    system_prompt_path: str = ""
    #: Text added to the reviewer's instructions under "Project-specific rules" -- what
    #: this project's reviewer should and should not report -- without replacing the
    #: whole system prompt (bug Bbeb0c7542f: the template rendered the block, but no
    #: config key ever filled it).
    extra_rules: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)
    #: Extra environment for a kind="command" reviewer (e.g. a per-reviewer API key).
    env: dict[str, str] = field(default_factory=dict)

    def resolved_family(self) -> str:
        """Declared family wins over guessed. ``""`` means nobody has classified this
        model — `ddflow reviewers list` flags it, because an unclassified reviewer
        cannot satisfy the different-family requirement."""
        return self.family or family_of(self.model)

    def launch_spec(self) -> LaunchSpec:
        known = set(LaunchSpec.__dataclass_fields__)
        unknown = set(self.launch) - known
        if unknown:
            raise ValueError(
                f"reviewer {self.name!r}: unknown launch field(s) {sorted(unknown)}. "
                f"Known: {sorted(known)}"
            )
        return LaunchSpec(**self.launch)

    def api_key(self) -> str:
        # The key is read from the environment, never stored in the config file. A key
        # in a committed TOML is a leaked key, and this config is meant to be committed.
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""


@dataclass
class Finding:
    severity: str
    title: str
    detail: str = ""
    location: str = ""
    #: The chunk (1-based) the finding came from, so a re-review of that chunk can
    #: replace it and leave the other chunks' findings standing.
    chunk: int = 0
    #: Digest of the finding's FULL text; what a triage is keyed by (`finding_digest`).
    digest: str = ""


def finding_digest(severity: str, location: str, title: str, detail: str) -> str:
    """A finding's identity for triage: its whole text, so a re-review's finding is the
    same one only when the reviewer said the same thing (decision D-review-triage).

    Word-for-word identical findings -- one claim made twice -- share a digest, so a
    triage of one is a triage of both. Telling them apart by position was tried and
    dropped: a position moves when an earlier chunk is re-reviewed, and the triage then
    lands on the wrong copy (three reviewers, d2b337f)."""

    text = "\x1f".join((severity.upper(), location.strip(), title.strip(), detail.strip()))
    return content_digest(text, errors="replace", length=16)


#: How much of a finding's detail the gate evidence keeps for a later merge.
FINDING_DETAIL_KEPT = 2000


@dataclass
class ReviewResult:
    reviewer: str
    model: str
    family: str
    status: int = UNAVAILABLE
    findings: list[Finding] = field(default_factory=list)
    reason: str = ""
    chunks_total: int = 0
    chunks_reviewed: int = 0
    chunks_off_contract: int = 0
    elapsed_s: float = 0.0
    #: How many rounds the sent chunks' FIRST copies needed at the reviewer's concurrency
    #: (`_waves`; a hedge copy answering a failed first copy may add a round it does not count):
    #: `elapsed_s / waves` is the reviewer's time per request, which the adaptive flow
    #: controller reads as reviewer latency (bug B1c5dbe3103).
    waves: int = 1
    raw: str = ""
    #: One entry per chunk that produced no review: ``{"chunk", "files", "reason"}``.
    #: Every one of them, not the first -- a PARTIAL that names one cause for three
    #: missing chunks hides which files nobody reviewed (bug Bd2332f8f2a).
    unreviewed: list[dict[str, Any]] = field(default_factory=list)
    #: The chunks (1-based) that came back with a verdict.
    reviewed: list[int] = field(default_factory=list)
    #: The chunks this run was asked for (`ddflow review --chunk`); empty = all.
    requested: list[int] = field(default_factory=list)
    #: What the chunk numbers refer to: the reviewed diff's digest and the chunk size it
    #: was cut with. A re-review of chunk N merges only into a record of the SAME cut.
    diff_sha: str = ""
    max_chunk_chars: int = 0

    @property
    def label(self) -> str:
        return {
            REVIEWED: "REVIEWED",
            ERROR: "ERROR",
            UNAVAILABLE: "UNAVAILABLE",
            PARTIAL: "PARTIAL",
        }[self.status]

    def coverage(self) -> str:
        return f"{self.chunks_reviewed}/{self.chunks_total} chunk(s) reviewed" + (
            f", {self.chunks_off_contract} off-contract" if self.chunks_off_contract else ""
        )

    def evidence(self) -> dict[str, Any]:
        return {
            "reviewer": self.reviewer,
            "model": self.model,
            "family": self.family,
            "status": self.label,
            "findings": len(self.findings),
            "coverage": self.coverage(),
            "elapsed_s": round(self.elapsed_s, 1),
            "reason": self.reason,
            "titles": [f.title for f in self.findings[:10]],
            "unreviewed": self.unreviewed,
            "reviewed": self.reviewed,
            "diff_sha": self.diff_sha,
            "max_chunk_chars": self.max_chunk_chars,
            "chunks_total": self.chunks_total,
            "waves": self.waves,
            "chunk_findings": [
                {
                    "chunk": f.chunk,
                    "digest": f.digest,
                    "severity": f.severity,
                    "title": f.title,
                    "location": f.location,
                    "detail": f.detail[:FINDING_DETAIL_KEPT],
                }
                for f in self.findings
            ],
        }


# -- configuration ---------------------------------------------------------------------


def load_reviewers(root: Path) -> list[Reviewer]:
    """Read ``[[reviewer]]`` blocks from ``.ddflow/config.toml``.

    Reviewers live in the same file as everything else so a project has ONE place to
    configure. ``.ddflow/reviewers.toml`` is also read if present, for operators who
    prefer to split it; entries merge by name, with the dedicated file winning.
    """
    from ..config import Config, _is_code_tree
    from ..infra import tomlcfg

    blocks = tomlcfg.overlay_array(
        tomlcfg.config_paths(root, "reviewers.toml"),
        "reviewer",
        Reviewer,
        key="name",
        fallback_key="model",
        # A newer checkout's reviewer knob (hedge, max_concurrency, ...) warns and is
        # skipped by older code, instead of failing every review (B6f757e18cf).
        lenient=not _is_code_tree(root),
    )
    # The project's own family map, not just the shipped one. `resolved_family` had no
    # Config in scope, so it always used `FAMILY_HINTS` while `reviewer_independence`
    # used `[agent].families` — a project that taught the map its in-house model name
    # got it honoured by the check that decides whether a review counted and ignored by
    # `ddflow reviewers list` and by the family recorded on the result. Two surfaces,
    # one question, two answers. Resolving it here means there is still exactly one map.
    try:
        families = Config.load(root).agent.families
    except Exception:  # a broken config is reported by Config.load's own caller
        families = None
    out = []
    for name, block in blocks.items():
        rev = Reviewer(**{**block, "name": name})
        if not rev.family and families is not None:
            rev.family = family_for(rev.model, families)
        out.append(rev)
    return out


def reviewers_for(reviewers: list[Reviewer], gate: str) -> list[Reviewer]:
    return [r for r in reviewers if r.enabled and gate in r.gates]


# -- probing ---------------------------------------------------------------------------


class _Cancel:
    """What one hedged copy is blocked on, so the winning copy can stop it.

    A losing copy that is merely ignored keeps the server generating -- up to
    `max_tokens` of reasoning -- and keeps a thread the CLI must wait for before it can
    exit. So cancelling closes the thing it is blocked on: the socket of an HTTP
    reviewer (the server sees the disconnect and aborts the request), or the process
    group of a command reviewer. Things attached after cancellation are closed at once.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: list[Any] = []
        self.cancelled = False

    def attach(self, thing: Any) -> None:
        with self._lock:
            if not self.cancelled:
                self._held.append(thing)
                return
        _abort(thing)

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            held, self._held = self._held, []
        for thing in held:
            _abort(thing)


def _abort(thing: Any) -> None:
    try:
        if isinstance(thing, subprocess.Popen):
            P.kill_group(thing)  # the shell AND the reviewer it started
        else:
            thing.shutdown(socket.SHUT_RDWR)
    except (OSError, ProcessLookupError):
        pass


#: The copy the current thread is running, if any: set by the review scheduler around
#: each `_chat`, read where a socket or process is created.
_running = threading.local()


def _attach_to_running(thing: Any) -> None:
    token = getattr(_running, "cancel", None)
    if token is not None:
        token.attach(thing)


class _TrackedHTTP(http.client.HTTPConnection):
    def connect(self) -> None:
        super().connect()
        _attach_to_running(self.sock)


class _TrackedHTTPS(http.client.HTTPSConnection):
    def connect(self) -> None:
        super().connect()
        _attach_to_running(self.sock)


class _TrackedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_TrackedHTTP, req)


class _TrackedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_TrackedHTTPS, req, context=self._context)


#: `urlopen`'s own opener -- proxies, redirects, TLS defaults -- except that each
#: connection's socket is handed to the copy that opened it, so it can be cancelled.
_OPENER = urllib.request.build_opener(_TrackedHTTPHandler, _TrackedHTTPSHandler)


def _open(target: str | urllib.request.Request, *, timeout: float):
    """`urlopen`, for http(s) only. Every reviewer HTTP call goes through here.

    `urlopen` follows any scheme it knows, `file:` included, and reviewer URLs come from
    configuration: `base_url = "file:///..."` -- a typo, or a config someone else wrote --
    made `probe_endpoint` read a local file and report its contents as model ids
    (reproduced in tests/test_review_urls.py). Refused as a `URLError`, which every
    caller already treats as "unreachable". Flagged by bandit B310.
    """
    url = target.full_url if isinstance(target, urllib.request.Request) else target
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise urllib.error.URLError(f"only http(s) reviewer endpoints; refusing {scheme!r}")
    return _OPENER.open(target, timeout=timeout)  # nosec B310 -- scheme checked above


def probe_endpoint(base_url: str, timeout_s: float = 4.0) -> list[str]:
    """Model ids served at ``base_url``, or [] if nothing answers.

    Deliberately short-timeout and exception-swallowing: this runs against a list of
    candidate ports, and a closed port must cost milliseconds, not a stack trace.
    """
    from ..infra.container import rewrite_localhost

    url = rewrite_localhost(base_url).rstrip("/") + "/models"
    try:
        with _open(url, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return []
    return [m.get("id", "") for m in body.get("data", []) if m.get("id")]


def detect(
    endpoints: tuple[tuple[str, str], ...] = WELL_KNOWN_ENDPOINTS,
) -> list[tuple[str, str, list[str]]]:
    """(base_url, label, models) for every candidate endpoint that answers."""
    out = []
    for url, label in endpoints:
        models = probe_endpoint(url)
        if models:
            out.append((url, label, models))
    return out


# -- the review itself -------------------------------------------------------------------

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


def _chat(rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    """One completion, whatever the backend. Returns (content, error). Never raises.

    Four backends, because "any LLM" means four wire formats in practice: the
    OpenAI-compatible one that most providers and every local server speak, Anthropic's
    Messages API, Google's generateContent, and -- the universal fallback -- an
    arbitrary command reading stdin. The last one is what makes a model with no HTTP
    API at all usable as a reviewer.
    """
    if rev.kind == "command":
        return _chat_command(rev, system, user, timeout_s)
    if rev.kind == "anthropic":
        return _chat_anthropic(rev, system, user, timeout_s)
    if rev.kind == "gemini":
        return _chat_gemini(rev, system, user, timeout_s)
    if rev.kind != "openai":
        return "", (
            f"unknown reviewer kind {rev.kind!r}; expected one of "
            f"openai, anthropic, gemini, command"
        )
    return _chat_openai(rev, system, user, timeout_s)


def _tool_moved(before: dict[str, str] | None) -> str:
    """Did a command reviewer, now finished or killed, leave git changed? (B5ce30dd94d)"""
    return git_state_change(before, git_state(os.getcwd()))


def _chat_command(rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    """Run a CLI, prompt on stdin, reply on stdout.

    Deliberately dumb and therefore universal. A non-zero exit is an error (the review
    did not happen), and empty stdout is an error even on exit 0 -- a tool that printed
    nothing reviewed nothing, and treating that as "no findings" is the vacuous pass.
    """
    cmd = rev.command.replace("{model}", rev.model)
    if not cmd.strip():
        return "", "kind='command' but no `command` is configured"
    # `gates._missing_executable`, not a second implementation of it. The copy here
    # inspected only `cmd[:1]` for shell characters, so `FOO=bar claude -p` split to a
    # head of `FOO=bar` and reported a false UNAVAILABLE; it had no builtin allowlist;
    # and it let `shlex.split`'s ValueError on an unbalanced quote escape a function
    # whose entire contract is to turn every way of not-reviewing into a reported one.
    missing = _missing_executable(cmd, {**os.environ, **rev.env}.get("PATH"))
    if missing:
        return "", (
            f"executable {missing!r} is not on PATH -- the reviewer could not run. "
            f"This is NOT a clean review."
        )
    # A reviewer must leave git as it found it (B5ce30dd94d); its cwd is this process's.
    state_before = git_state(os.getcwd())
    try:
        # Its own process group, so a timeout or a winning copy kills the reviewer the
        # shell started, not just the shell.
        p = P.popen(
            cmd,
            # bandit B604: a CLI reviewer IS a shell command line the operator configured.
            shell=True,  # nosec B604
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **rev.env},
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        return "", f"could not execute: {exc}"
    _attach_to_running(p)
    try:
        stdout, stderr = p.communicate(f"{system}\n\n{user}", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _abort(p)
        p.communicate()
        return "", _tool_moved(state_before) or f"command timed out after {timeout_s:.0f}s"
    except OSError as exc:
        _abort(p)
        return "", _tool_moved(state_before) or f"could not execute: {exc}"
    moved = _tool_moved(state_before)
    if moved:
        return "", moved
    out = (stdout or "").strip()
    if p.returncode != 0:
        return "", (f"command exited {p.returncode}: {((stderr or '') + out).strip()[:300]}")
    if not out:
        return "", "command produced no output on stdout"
    return out, ""


def _cut_off(exc: Exception) -> str:
    """An `http.client.HTTPException`, which is no `OSError`: most often a response that
    stopped mid-read (`IncompleteRead`). Never a `TRUNCATED`, so never retried as one.

    The everyday cause is hedging: the winning copy shuts a loser's socket while the
    loser is reading its answer. Uncaught, it escaped `_chat` and crashed the whole
    review, whose contract is never to raise (bug Bf948d29d37).
    """
    if isinstance(exc, http.client.IncompleteRead):
        return f"connection cut off mid-response: {exc!r}"
    # The rest of the family is the endpoint, not the reply: an InvalidURL from a bad
    # base_url port, a BadStatusLine from a proxy that does not speak HTTP (critic).
    return f"bad HTTP exchange with the endpoint: {exc!r}"


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
    from ..infra.container import rewrite_localhost

    req = urllib.request.Request(
        rewrite_localhost(url),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with _open(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), ""
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}"
    except TimeoutError:
        return None, _no_answer_in_time(timeout_s)
    except (urllib.error.URLError, OSError) as exc:
        return None, f"unreachable: {exc}"
    except http.client.HTTPException as exc:
        return None, _cut_off(exc)
    except (ValueError, json.JSONDecodeError) as exc:
        return None, f"bad response body: {exc}"


#: Marks a request the endpoint ACCEPTED but did not answer within its timeout.
DID_NOT_CONVERGE = "DID NOT CONVERGE:"


def _no_answer_in_time(timeout_s: float) -> str:
    """A read timeout, told apart from an endpoint that is not there.

    `urlopen` wraps a CONNECT timeout in `URLError` -- nothing is listening, which is
    "unreachable". A timeout raised bare came after the request was sent and accepted:
    the server was still generating. Reported as "unreachable: timed out" it sent
    operators to check an endpoint that was fine, while the chunk was deterministically
    running to `max_tokens` (bug B338a8bb598: 32 000 tokens at under 36 tok/s is more
    than the 900 s timeout).
    """
    return (
        f"{DID_NOT_CONVERGE} the endpoint accepted the request but no answer came within "
        f"{timeout_s:.0f}s -- the model was still generating, most likely toward "
        f"max_tokens. Raise [[reviewer]].timeout_s, lower max_tokens, or lower "
        f"max_chunk_chars so each chunk needs less thinking. The endpoint is reachable."
    )


def _temperature(rev: Reviewer) -> dict[str, float]:
    """The request's temperature field: absent unless the reviewer entry sets one."""
    return {} if rev.temperature is None else {"temperature": rev.temperature}


#: Below this a reasoning model is sampled colder than vendors recommend (DeepSeek and
#: Qwen thinking modes: 0.6-1.0), which is where looping-to-the-budget was measured.
_COLD = 0.6

#: Marks a `_truncated` reply, so a chunk lost to it can be told apart and retried.
TRUNCATED = "TRUNCATED:"


def _truncated(rev: Reviewer, *, reasoning_tokens: object = None) -> str:
    """The one way this package explains a reply that ran out of budget mid-thought.

    Written three times before — once per backend — and already drifted: the OpenAI
    copy named `max_chunk_chars` and reported reasoning-token usage, the Gemini copy
    named neither. A reviewer that hit this returns UNAVAILABLE, and the difference
    between "raise max_tokens" and "lower max_chunk_chars" is the difference between
    a fix and another hour of the same failure, so the remedy has to be complete
    wherever it is read.

    Measured, not guessed: on Qwen3.8-Flash-Next-FP8 over a 30 KB diff, a 6,000-token
    budget produced ZERO characters of content — the whole budget went to reasoning.
    That is why the default is 32,000.
    """
    detail = ""
    if reasoning_tokens not in (None, "", 0):
        detail = f" ({reasoning_tokens} of them reasoning)"
    cold = (
        f"[[reviewer]].temperature = {rev.temperature} is below what most reasoning "
        f"models are tuned for, and a model sampled cold can loop until the budget is "
        f"gone: remove it (the model's own default applies) or set the vendor's "
        f"recommended value. Otherwise raise "
        if rev.temperature is not None and rev.temperature < _COLD
        else "If the model's vendor recommends a temperature, check the server applies "
        "it ([[reviewer]].temperature sets one explicitly). Otherwise raise "
    )
    return (
        f"{TRUNCATED} the model consumed all {rev.max_tokens} tokens{detail} before "
        f"emitting any answer. This is UNAVAILABLE, not a clean review. {cold}"
        f"[[reviewer]].max_tokens, or lower [[reviewer]].max_chunk_chars so each "
        f"chunk needs less thinking."
    )


def _chat_anthropic(rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    """Anthropic Messages API: system is a TOP-LEVEL field, not a message."""
    key = rev.api_key()
    if not key:
        return "", (f"no API key: set ${rev.api_key_env or 'ANTHROPIC_API_KEY'} in the environment")
    body, err = _post_json(
        rev.base_url.rstrip("/") + "/messages",
        {
            "model": rev.model,
            "max_tokens": rev.max_tokens,
            **_temperature(rev),
            "system": system,
            "messages": [{"role": "user", "content": user}],
            **rev.extra_body,
        },
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        timeout_s,
    )
    if err:
        return "", err
    try:
        blocks = body["content"]
    except (KeyError, TypeError):
        return "", f"unexpected response shape: {str(body)[:300]}"
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if body.get("stop_reason") == "max_tokens" and not parse(text)[1]:
        return "", _truncated(rev)
    return text, ""


def _chat_gemini(rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    """Google generateContent: the key goes in the query string, not a header."""
    key = rev.api_key()
    if not key:
        return "", f"no API key: set ${rev.api_key_env or 'GEMINI_API_KEY'}"
    url = (
        f"{rev.base_url.rstrip('/')}/models/{rev.model}:generateContent"
        f"?key={urllib.parse.quote(key)}"
    )
    body, err = _post_json(
        url,
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {**_temperature(rev), "maxOutputTokens": rev.max_tokens},
            **rev.extra_body,
        },
        {},
        timeout_s,
    )
    if err:
        return "", err
    try:
        cand = body["candidates"][0]
    except (KeyError, IndexError, TypeError):
        fb = (body or {}).get("promptFeedback", {})
        return "", f"no candidate returned{f' ({fb})' if fb else ''}"
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])).strip()
    if cand.get("finishReason") == "MAX_TOKENS" and not parse(text)[1]:
        return "", _truncated(rev)
    return text, ""


def _chat_openai(rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    payload: dict[str, Any] = {
        "model": rev.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        **_temperature(rev),
        "max_tokens": rev.max_tokens,
        **rev.extra_body,
    }
    from ..infra.container import rewrite_localhost

    req = urllib.request.Request(
        rewrite_localhost(rev.base_url).rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {rev.api_key()}"} if rev.api_key() else {}),
        },
    )
    try:
        with _open(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return "", f"HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}"
    except TimeoutError:
        return "", _no_answer_in_time(timeout_s)
    except (urllib.error.URLError, OSError) as exc:
        return "", f"unreachable: {exc}"
    except http.client.HTTPException as exc:
        return "", _cut_off(exc)
    except (ValueError, json.JSONDecodeError) as exc:
        return "", f"bad response body: {exc}"
    try:
        choice = body["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError, TypeError):
        return "", f"unexpected response shape: {str(body)[:300]}"
    content = (msg.get("content") or "").strip()
    if choice.get("finish_reason") == "length" and not parse(content)[1]:
        # Naming the remedy matters: "empty completion" sends an operator hunting for a
        # broken endpoint, when the endpoint is fine and the token budget is too small
        # for a model that thinks before it answers.
        used = (body.get("usage") or {}).get("completion_tokens_details") or {}
        return "", _truncated(rev, reasoning_tokens=used.get("reasoning_tokens"))
    # Reasoning models split deliberation from the answer. We want the ANSWER; if the
    # model spent its whole budget reasoning and returned no content, that is an empty
    # completion (UNAVAILABLE), not a review that found nothing.
    return content, ""


def parse(text: str) -> tuple[list[Finding], bool]:
    """(findings, on_contract). Off contract means no STATUS: block at all."""
    status = _STATUS_RE.search(text or "")
    findings = [
        Finding(
            severity=m.group(1).upper(),
            location=(m.group(2) or "").strip(),
            title=(m.group(2) or "").strip() or " ".join((m.group(3) or "").split())[:80],
            detail=(m.group(3) or "").strip(),
        )
        for m in _FINDING_RE.finditer(text or "")
    ]
    return findings, status is not None


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


def _race(
    rev: Reviewer,
    system: str,
    users: list[str],
    started: float,
    *,
    on_settled: Callable[[int, tuple[str, str]], None] | None = None,
    on_tick: Callable[[list[int]], None] | None = None,
    tick_s: float = 0,
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
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

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
        if i in settled:
            return None  # another copy already won; never start this one
        remaining = rev.total_budget_s - (time.time() - started)
        if remaining <= 0:
            return "", f"total budget {rev.total_budget_s}s exhausted before it started"
        _running.cancel = tokens[(i, c)]
        try:
            return _chat(rev, system, users[i], min(rev.timeout_s, remaining))
        finally:
            _running.cancel = None

    order = [(i, c) for c in range(hedge) for i in range(n)]
    with ThreadPoolExecutor(max_workers=min(cap, len(order))) as pool:
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
                    for other in range(hedge):
                        if other != c:
                            tokens[(i, other)].cancel()
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


def _retry_truncated(
    rev: Reviewer,
    system: str,
    chunks: list[str],
    results: list[tuple[str, str]],
    render,
    started: float,
    **race_kw: Any,
) -> list[tuple[str, str]]:
    """Give a chunk that ran out of budget on EVERY copy one more round.

    A reasoning model's thinking length is random per call and grows with the input,
    so a chunk lost to `TRUNCATED` is retried once: in halves when it splits (by file,
    then by hunk, as `split_diff` always does), whole when it does not. The chunk counts
    as reviewed only if every part comes back on contract; the parts' replies are
    joined into the chunk's one reply, so chunk counts and reports are unchanged. One
    round only, inside `total_budget_s` -- this is a second draw, not a loop.
    """
    lost = [i for i, (_c, err) in enumerate(results) if err.startswith(TRUNCATED)]
    if not lost:
        return results
    parts: list[tuple[int, str]] = []
    for i in lost:
        pieces = split_diff(chunks[i], max(1, (len(chunks[i]) + 1) // 2))
        # Halves missing a file would let the chunk count as reviewed without it, so a
        # chunk whose halves do not carry every file header is retried whole. split_diff
        # no longer drops a hunkless section (B779270c994); this stays as the invariant
        # check, not as a workaround.
        headers = unidiff.headers(chunks[i])
        # No header at all is not "every header kept": without one there is nothing to
        # check the halves against, so the chunk goes again whole (roborev).
        if not pieces or not headers or not all(any(h in p for p in pieces) for h in headers):
            pieces = [chunks[i]]
        parts += [(i, piece) for piece in pieces]
    # `render` is the renderer every first copy already went through.
    users = [render(piece, i + 1) for i, piece in parts]
    again = _race(rev, system, users, started, **race_kw)
    out = list(results)
    for i in lost:
        mine = [again[k] for k, (j, _p) in enumerate(parts) if j == i]
        failed = next(((c, e) for c, e in mine if e or not c or not parse(c)[1]), None)
        if failed is None:
            out[i] = ("\n\n".join(c for c, _e in mine), "")
            continue
        note = f"retried once in {len(mine)} part(s), still unreviewed"
        _c, err = failed
        # An empty or off-contract part has no error of its own: report the truncation.
        out[i] = ("", f"{err} ({note})" if err else f"{results[i][1]} ({note})")
    return out


class _Progress:
    """The lines a running review prints, and the tick it hands its caller.

    One line per chunk as it settles, and one naming what is still awaited every tick:
    a review that said nothing for half an hour was killed as hung (bug B9d8bd466c3).
    """

    def __init__(self, files, started, timeout_s, on_progress=None, on_tick=None) -> None:
        self.files, self.started, self.timeout_s = files, started, timeout_s
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
            f"{time.time() - self.started:.0f}s elapsed; one request may take up to "
            f"{self.timeout_s}s"
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


def ensure_running(rev: Reviewer, *, on_log=None) -> tuple[bool, str]:
    """Start a local server if one is configured and is not already answering.

    Returns ``(available, note)``. Never raises, and never leaves a half-started process
    behind: if the readiness probe never passes, the child is terminated and the reason
    is returned. A reviewer stuck "starting" forever is indistinguishable from one that
    is down, except that it also holds a process.
    """
    import time as _time

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
    try:
        proc = P.popen(
            cmd,
            # bandit B604: a reviewer's start command IS a shell command line the operator
            # configured.
            shell=True,  # nosec B604
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        return False, f"could not start {cmd!r}: {exc}"

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
    from ..infra.container import rewrite_localhost

    try:
        with _open(rewrite_localhost(url), timeout=timeout_s) as r:
            return _HTTP_OK_FLOOR <= r.status < _HTTP_SERVER_ERROR
    except urllib.error.HTTPError as exc:
        return exc.code < _HTTP_SERVER_ERROR
    except (urllib.error.URLError, OSError):
        return False


def review(  # noqa: PLR0913 -- one reviewer run: what, how, and four callbacks
    rev: Reviewer,
    diff: str,
    intent: str,
    *,
    context: str = "",
    repo: Path | None = None,
    prompt_overrides: dict[str, str] | None = None,
    only: list[int] | None = None,
    on_progress: Callable[[str], None] | None = None,
    on_tick: Callable[[], None] | None = None,
    tick_s: float = 60,
    on_chunk: Callable[[int, str], None] | None = None,
) -> ReviewResult:
    """Run one reviewer over one diff. Never raises; encodes everything in the status.

    ``on_progress`` gets a line as each chunk settles and, every ``tick_s`` while chunks
    are in flight, one naming what is still awaited; ``on_tick`` is called at the same
    cadence, on the caller's thread (the api renews the caller's lease there).
    ``on_chunk(n, reply)`` gets each chunk's whole reply the moment it arrives, so a
    caller can keep the finding bodies even if the run is cut short (bug B206).
    ``only`` names the chunks (1-based, as a full run numbers them) to send; the rest
    are neither sent nor counted as lost -- `merge_rerun` folds such a run into the
    record of the full one.
    """
    res = ReviewResult(reviewer=rev.name, model=rev.model, family=rev.resolved_family())
    started = time.time()

    problem = _preflight(rev, diff, res)
    if problem is not None:
        return problem

    stripped = strip_hunk_context(diff)
    chunks = split_diff(stripped, rev.max_chunk_chars)
    res.chunks_total = len(chunks)
    res.diff_sha, res.max_chunk_chars = diff_digest(stripped), rev.max_chunk_chars
    files = [chunk_files(c) for c in chunks]
    wanted = sorted(set(only or ())) or list(range(1, len(chunks) + 1))
    outside = [n for n in wanted if not 1 <= n <= len(chunks)]
    if outside:
        res.status = ERROR
        res.reason = f"no chunk {outside} -- this diff has chunks 1..{len(chunks)}"
        return res
    res.requested = sorted(set(only or ()))

    from ..services import prompts as P

    overrides = dict(prompt_overrides or {})
    if rev.system_prompt_path:
        # A per-reviewer override still wins: two reviewers may want different
        # instructions, which a single project-wide template cannot express.
        overrides["review_system"] = rev.system_prompt_path
    try:
        system = P.render(
            P.resolve("review_system", repo, overrides), extra_rules=rev.extra_rules.strip()
        )
        user_tmpl = P.resolve("review_user", repo, overrides)
    except P.TemplateError as exc:
        res.status, res.reason = ERROR, str(exc)
        return res

    def render(chunk: str, i: int) -> str:
        # ONE renderer for the first pass and the truncation retry, so they cannot drift.
        return P.render(
            user_tmpl,
            intent=intent,
            context=context,
            diff=chunk,
            fence=diff_fence(chunk),
            chunk_index=i,
            chunk_total=len(chunks),
        )

    try:
        users = [render(chunks[n - 1], n) for n in wanted]
    except P.TemplateError as exc:
        res.status, res.reason = ERROR, f"review_user template: {exc}"
        return res

    progress = _Progress(files, started, rev.timeout_s, on_progress, on_tick)

    def _keep(n: int, got: tuple[str, str]) -> None:
        if on_chunk and got[0]:
            on_chunk(n, got[0])

    # `_race` and the retry index the SENT chunks (positions in `wanted`); progress and
    # absorption speak chunk numbers.
    res.waves = _waves(rev, len(users))
    first = _race(
        rev,
        system,
        users,
        started,
        on_settled=lambda k, got: (_keep(wanted[k], got), progress.settled(wanted[k] - 1, got)),
        on_tick=lambda waiting: progress.tick([wanted[k] - 1 for k in waiting]),
        tick_s=tick_s,
    )
    # The retry races PARTS of the lost chunks, so its indices are not chunk numbers:
    # its ticks name the chunks being retried instead (critic).
    retried = [wanted[k] - 1 for k, (_c, err) in enumerate(first) if err.startswith(TRUNCATED)]
    results = _retry_truncated(
        rev,
        system,
        [chunks[n - 1] for n in wanted],
        first,
        lambda chunk, k: render(chunk, wanted[k - 1]),
        started,
        on_tick=lambda _parts: progress.tick(retried, " on retry"),
        tick_s=tick_s,
    )
    for k, (before, after) in enumerate(zip(first, results, strict=True)):
        if before != after:
            _keep(wanted[k], after)
            progress.settled(wanted[k] - 1, after, " on retry")

    all_raw: list[str] = []
    # Absorbed in CHUNK order whatever order they finished in, so the findings, the raw
    # transcript and the reported failures do not depend on which copy was quick.
    for n, (content, err) in zip(wanted, results, strict=True):
        _absorb_chunk(res, n, content, err, all_raw, files[n - 1])
    res.raw = "\n\n---\n\n".join(all_raw)
    res.elapsed_s = time.time() - started
    _settle(res)
    return res


def _settle(res: ReviewResult) -> None:
    """Status and reason from coverage: every chunk, some, or none."""
    _name_unreviewed(res)
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
    again = set(res.requested)
    kept = {int(n) for n in prior["reviewed"]} - again
    res.reviewed = sorted(kept | set(res.reviewed))
    res.chunks_reviewed = len(res.reviewed)
    res.findings = sorted(
        [
            Finding(
                severity=f.get("severity", ""),
                title=f.get("title", ""),
                detail=f.get("detail", ""),
                location=f.get("location", ""),
                chunk=int(f.get("chunk", 0)),
                digest=f.get("digest", ""),
            )
            for f in prior.get("chunk_findings", [])
            if int(f.get("chunk", 0)) not in again
        ]
        + res.findings,
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
