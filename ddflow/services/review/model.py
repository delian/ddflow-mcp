"""Reviewer data model, configuration, and dispatch.

Split out of ``ddflow/services/review.py``: constants, the ``Reviewer``/``Finding``/
``ReviewResult`` dataclasses, ``load_reviewers``, and the pool/route/layer dispatch
stage (D-unify kind-pipeline). No network or process I/O live here.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import Config, _is_code_tree, family_for
from ...core.digest import content_digest
from ...infra import tomlcfg
from .. import modelreg as _modelreg
from .. import review_keys

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


#: Ready-made settings for the providers people actually use, READ from the model catalog
#: (`modelreg.presets`, DRV-model-catalog). `ddflow reviewers add --preset openai` writes the
#: block; nothing here is required, it just saves an operator looking up a base_url and
#: guessing which env var the key lives in. `modelreg.presets` returns the catalog's routes
#: exactly as this table carried them (the parity test guards the drift).
PRESETS: dict[str, dict[str, Any]] = _modelreg.presets(_modelreg.load())


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
    #: Machine-local reviewers file only: a file holding the key, read when the variable
    #: is unset (see `review_keys`). The path is data; the key never is.
    api_key_file: str = ""
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
        return review_keys.resolve_key(self.api_key_env, self.api_key_file).value

    def key_result(self, default_env: str = "") -> review_keys.KeyResult:
        """The key, or why there is none (a reason naming the variable and file, never a value)."""
        return review_keys.resolve_key(self.api_key_env, self.api_key_file, default_env)


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
    #: The reviewer that raised it, and its family: a panel record holds every heard
    #: reviewer's findings, so `merge_rerun` must be able to tell a peer reviewer's
    #: finding of a re-run chunk from the one being re-run (bug B-chunk-rerun-drops-
    #: peer-findings).
    reviewer: str = ""
    family: str = ""


#: A reply or transport error that says the REVIEWER's own quota/exhaustion wall was hit --
#: the account pays, not the diff's shape. Shedding sends the gate to a DIFFERENT endpoint
#: (a different account), which fixing the chunk size, the timeout or the key cannot.
#: Deliberately NOT the token-budget truncation `_truncated` (a chunk lost to its OWN size,
#: retried in halves): that is the diff's shape, not the endpoint's account. Account walls
#: are named: an HTTP 403/429 status code (with or without an `HTTP/1.1` prefix, e.g.
#: httpx's bare ``403 Forbidden``), the word `quota` (a token, so "insufficient_quota" and
#: "exceeded your quota" match but "quotation" does not), and a `rate`/`usage`/`request`/
#: `concurrency` limit (space/tab/hyphen/underscore separator, the endpoint's account). A
#: BARE "limit exceeded" is NOT a signal: a `max_tokens`/context limit is the chunk's own
#: shape, not the endpoint.
_EXHAUSTION_RE = re.compile(
    r"HTTP(?:/\d\.\d)?\s*(?:403|429)\b|\b(?:403|429)\b|"
    r"(?<![A-Za-z])quota(?![A-Za-z])|\b(?:rate|usage|request|concurrency)[ \t_-]?limits?(?![A-Za-z])",
    re.IGNORECASE,
)


def classify_exhaustion(text: str) -> str:
    """A non-empty reason when ``text`` says a REVIEWER hit its own quota/exhaustion wall.

    ``"HTTP 403"``, ``"quota"``, ``"rate limit"``, ``"rate limit exceeded"`` -- the signal
    that the endpoint's account (a 5-hour limit, an exhausted key) can no longer review,
    which no chunk tuning fixes and a different reviewer endpoint might. This is the marker
    the failover shed reads, and `doctor` reports, instead of the gate being silently
    `unavailable`. ``""`` for anything else: a clipped reply, an off-contract answer, a
    chunk too large for ``max_tokens`` -- a BARE "limit exceeded" counts as nothing, since
    it cannot be told from the chunk's own ``max_tokens``/context limit.
    """
    m = _EXHAUSTION_RE.search(text or "")
    if not m:
        return ""
    hit = m.group(0)
    return hit.upper() if hit.upper().startswith("HTTP") else " ".join(hit.split()).lower()


def degraded_marks(state: Any) -> dict[str, dict[str, Any]]:
    """reviewer name -> its latest quota/exhaustion mark, from recorded review evidence.

    The mark is written by ``api.review._review_gate`` into the gate record it appends
    (the committed "approvals log" record) as ``review_degraded``, so it is derived from
    the log, never a second store. One row per reviewer, newest last, so the LAST mark
    for a name is the one in effect. Each row carries ``at`` (a ``time.time()`` epoch
    second) so a mark can EXPIRE: a degraded reviewer is shed only while its mark is
    younger than `[review].degraded_cooldown_s`, then re-probed (bug B066719c493).
    """
    marks: dict[str, dict[str, Any]] = {}
    for item in getattr(state, "items", {}).values():
        for run in getattr(item, "gate_history", ()) or ():
            evidence = getattr(run, "evidence", None) or {}
            for row in evidence.get("review_degraded", ()) or ():
                if row.get("reviewer"):
                    marks[row["reviewer"]] = row
    return marks


def active_degraded_marks(
    state: Any, cooldown_s: float, now: float | None = None
) -> dict[str, dict[str, Any]]:
    """The degraded marks still in force: a reviewer's latest mark younger than
    ``cooldown_s`` seconds. A mark AT or past the cooldown is expired -- the reviewer's
    rate window is presumed reset, so it is re-eligible and must be re-attempted (and
    re-marked if the wall is still up) rather than shed forever (bug B066719c493).
    ``cooldown_s`` <= 0 re-probes on every review (no mark blocks a run). A mark with no
    ``at`` (written before the fix carried a timestamp) is treated as EXPIRED, so a
    reviewer already marked degraded before this fix is re-probed once and re-marked with
    a timestamp if the wall is still up, instead of staying shed forever for lack of one.

    ``now`` is the caller's clock; it defaults to ``time.time()`` so a test can freeze it.
    """
    if cooldown_s <= 0:
        return {}
    now = time.time() if now is None else now
    return {
        name: row
        for name, row in degraded_marks(state).items()
        if row.get("at") is not None and now - row["at"] < cooldown_s
    }


def degraded_notes(state: Any, cooldown_s: float = 0.0) -> list[str]:
    """The doctor notes for reviewers a quota/exhaustion wall marked degraded and shed.

    Derived from ``review_degraded`` marks ``api.review`` recorded on gate results (the
    committed "approvals log" record): one line per degraded reviewer naming what was hit,
    so the operator sees the wall instead of a run of silently `unavailable` gates. Only a
    mark still WITHIN `[review].degraded_cooldown_s` is reported as shed; an expired mark
    is dropped, matching `active_degraded_marks` -- the reviewer is re-probed, not still
    shed (bug B066719c493). ``cooldown_s`` 0 (the default) sheds for no duration, so it
    reports nothing: the caller passes the live `[review].degraded_cooldown_s`.
    """
    return [
        f"reviewer {name!r} is degraded ({row.get('reason') or 'quota/exhaustion'}): shed "
        f"from its gates, re-probed when the mark expires"
        for name, row in sorted(active_degraded_marks(state, cooldown_s).items())
    ]


def finding_digest(severity: str, location: str, title: str, detail: str) -> str:
    """A finding's identity for triage: its whole text, so a re-review's finding is the
    same one only when the reviewer said the same thing (decision D-review-triage).

    Word-for-word identical findings -- one claim made twice -- share a digest, so a
    triage of one is a triage of both. Telling them apart by position was tried and
    dropped: a position moves when an earlier chunk is re-reviewed, and the triage then
    lands on the wrong copy (three reviewers, d2b337f)."""

    text = "\x1f".join((severity.upper(), location.strip(), title.strip(), detail.strip()))
    return content_digest(text, errors="replace", length=16)


#: A finding's severity as a number for the convergence floor (`[review].converge_floor`):
#: a later round blocks only on findings at or above the floor, so once the material
#: findings are fixed a reviewer raising new nits does not re-open the gate forever.
_SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


def blocking_findings(
    findings: list[Finding], *, converged: bool, floor: str, known: Iterable[str] = ()
) -> list[Finding]:
    """The findings of a review round that keep the gate open (the convergence floor).

    The FIRST full round blocks on every finding: the author has seen none of them yet, so a
    MEDIUM or LOW one is still new information that must be looked at. A LATER FULL re-review
    (``converged`` True, set by the caller only for a full re-review, bug Bf797745b5a) blocks
    only on findings at or above ``floor`` (`[review].converge_floor`): once the diff's
    material findings are fixed, a reviewer that keeps raising a new MEDIUM or LOW nit --
    often against a file unchanged since the last review, or a stale head -- does not
    re-open the gate. The below-floor findings are still recorded on the gate and triageable;
    they just do not hold the pass on their own. A delta or fix round does not cite this
    (it shows only NEW code, where any finding is a real defect), so it keeps blocking on
    every finding.

    ``known`` is the digests of the previous review's findings still standing (not refuted):
    a finding whose digest is in ``known`` is NOT a new nit, so the floor does not filter it
    -- an earlier below-floor finding the author neither fixed nor answered keeps blocking
    (bug B-floor-passes-repeated-untriaged-nit).
    """
    if not converged:
        return list(findings)
    limit = _SEVERITY_RANK.get((floor or "HIGH").upper(), _SEVERITY_RANK["HIGH"])
    known = {str(d) for d in known}
    # An unknown severity ranks as blocking (fail closed): only a known one below the floor
    # yields. A finding whose digest matches an earlier still-standing finding is not a new
    # nit, so the floor does not filter it.
    return [
        f
        for f in findings
        if _SEVERITY_RANK.get(f.severity.upper(), limit) >= limit or f.digest in known
    ]


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
    #: was cut with (the EFFECTIVE budget `_chunk_budget`, not the `Reviewer.max_chunk_chars`
    #: config). A re-review of chunk N merges only into a record of the SAME cut.
    diff_sha: str = ""
    max_chunk_chars: int = 0
    #: Non-empty when the reviewer itself hit a quota/exhaustion wall (`classify_exhaustion`):
    #: an HTTP 403/429, a vendor quota/rate-limit message. Not the diff's shape (a truncated
    #: chunk, an off-contract reply), which is retried or absorbed -- this is the ENDPOINT's
    #: account, which no chunk tuning fixes and a different reviewer's account might.
    degraded: str = ""

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
                    "reviewer": f.reviewer,
                    "family": f.family,
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
    review_keys.refuse_shared_key_files(root, tomlcfg.config_paths(root, "reviewers.toml"))
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


# -- dispatch: who reviews a gate, how it is routed, what they are told ----------------


@dataclass(frozen=True)
class Selected:
    """The reviewers a gate goes to, after the pool and route stages.

    ``agent`` is the named subagent the gate is routed to ("" when none is); ``notes``
    say why a candidate was left out (a member that would not be independent of the
    author, an endpoint at its concurrency limit), so a skip is never silent."""

    reviewers: list[Reviewer]
    agent: str = ""
    notes: list[str] = field(default_factory=list)


Pool = Callable[[list[Reviewer], str], Selected]
Route = Callable[[Selected, Any], Selected]
Layer = Callable[[str], str]


def pool_enabled(reviewers: list[Reviewer], gate: str) -> Selected:
    """The pool stage today: every enabled reviewer that lists ``gate``, in config order."""
    return Selected(reviewers_for(reviewers, gate))


def route_unrouted(selected: Selected, gdef: Any) -> Selected:
    """The route stage today: no gate names an agent, so the pool goes through unchanged."""
    return selected


@dataclass(frozen=True)
class ReviewerDispatch:
    """The one path from a gate to its reviewers: reviewer pool, then route, then context
    pack (D-unify kind-pipeline).

    Three stages, each a plain callable the task that owns it replaces -- the reviewer
    pool (spill and failover between independent endpoints), the route to a named
    subagent (`[gate.X] agent`), the rules a gate's prompt carries -- so none of them adds
    a fourth way to pick a reviewer or build a review prompt. The defaults are what
    `ddflow review` did before the stages existed:

    * ``pool(reviewers, gate)`` picks the candidates (`pool_enabled`);
    * ``route(selected, gdef)`` routes them (`route_unrouted`);
    * ``pack(context, layers)`` runs the prompt context through each layer in order (the
      previous findings of a re-review, then the project guidance that governs the item).
    """

    pool: Pool = field(default_factory=lambda: pool_enabled)
    route: Route = field(default_factory=lambda: route_unrouted)

    def select(self, reviewers: list[Reviewer], gate: str, gdef: Any = None) -> Selected:
        """Pool, then route."""
        return self.route(self.pool(reviewers, gate), gdef)

    @staticmethod
    def pack(context: str, layers: list[Layer]) -> str:
        """``context`` after each layer in turn (unchanged when there are none)."""
        for layer in layers:
            context = layer(context)
        return context
