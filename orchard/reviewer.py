"""Cross-family reviewers — a configured endpoint, not a prompt for the agent to obey.

The `critic` and `rubber_duck` gates exist because a same-family reviewer shares the
author's blind spots, so its agreement is not independent evidence. But a gate that only
*asks* the agent to go find a different model is a gate the agent grades itself on. This
module makes the reviewer a thing Orchard runs: you declare an OpenAI-compatible
endpoint and its pretraining family in TOML, and `orchard gate run <item> critic`
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

import json
import os
import re
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REVIEWED, ERROR, UNAVAILABLE, PARTIAL = 0, 1, 2, 3

#: Endpoints probed by `orchard reviewers detect`, with the label each usually means.
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

#: model-name substring -> pretraining family. Used to answer "is this reviewer
#: independent of the author?". An unrecognised model becomes its own family, which is
#: the safe direction: it can never be silently counted as matching the author.
FAMILY_HINTS: dict[str, str] = {
    "qwen": "alibaba",
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "codex": "openai",
    "gemini": "google",
    "gemma": "google",
    "llama": "meta",
    "mistral": "mistral",
    "mixtral": "mistral",
    "deepseek": "deepseek",
    "grok": "xai",
    "phi": "microsoft",
    "command": "cohere",
    "yi-": "01ai",
    "glm": "zhipu",
    "nemotron": "nvidia",
    "granite": "ibm",
    "kimi": "moonshot",
    "minimax": "minimax",
    "ernie": "baidu",
}


def family_of(model: str) -> str:
    low = (model or "").lower()
    for needle, fam in FAMILY_HINTS.items():
        if needle in low:
            return fam
    # Unknown models get a family of their own name, never "unknown" shared by all --
    # two different unrecognised models must not look like the same family to each
    # other, or the independence check silently passes on a pair that is not.
    return low.split("/")[-1] or "unknown"


@dataclass
class Reviewer:
    """One configured reviewer endpoint."""

    name: str
    base_url: str = ""
    model: str = ""
    family: str = ""
    api_key_env: str = ""
    gates: list[str] = field(default_factory=lambda: ["critic"])
    enabled: bool = True
    temperature: float = 0.3
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
    system_prompt_path: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)

    def resolved_family(self) -> str:
        return self.family or family_of(self.model)

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
    raw: str = ""

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
        }


# -- configuration ---------------------------------------------------------------------


def load_reviewers(root: Path) -> list[Reviewer]:
    """Read ``[[reviewer]]`` blocks from ``.orchard/config.toml``.

    Reviewers live in the same file as everything else so a project has ONE place to
    configure. ``.orchard/reviewers.toml`` is also read if present, for operators who
    prefer to split it; entries merge by name, with the dedicated file winning.
    """
    found: dict[str, Reviewer] = {}
    known = set(Reviewer.__dataclass_fields__)
    for path in (
        Path(root) / ".orchard" / "config.toml",
        Path(root) / ".orchard" / "reviewers.toml",
    ):
        if not path.is_file():
            continue
        data = tomllib.loads(path.read_text("utf-8"))
        for block in data.get("reviewer") or []:
            if not isinstance(block, dict):
                continue
            unknown = set(block) - known
            if unknown:
                raise ValueError(
                    f"unknown reviewer field(s) {sorted(unknown)} in {path}. Known: {sorted(known)}"
                )
            name = block.get("name") or block.get("model") or "reviewer"
            found[name] = Reviewer(**{**block, "name": name})
    return list(found.values())


def reviewers_for(reviewers: list[Reviewer], gate: str) -> list[Reviewer]:
    return [r for r in reviewers if r.enabled and gate in r.gates]


# -- probing ---------------------------------------------------------------------------


def probe_endpoint(base_url: str, timeout_s: float = 4.0) -> list[str]:
    """Model ids served at ``base_url``, or [] if nothing answers.

    Deliberately short-timeout and exception-swallowing: this runs against a list of
    candidate ports, and a closed port must cost milliseconds, not a stack trace.
    """
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
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

_SYSTEM = """\
You are reviewing a code change. Your job is to REFUTE it, not to praise it: find the \
input, interleaving, or environment that makes it WRONG.

Rules you must follow:
- If you are uncertain about something, report nothing about it. A reviewer rewarded \
for finding things finds things that are not there.
- Do not report style, formatting, naming, or missing docstrings. Only defects.
- Prefer concrete failure scenarios: "given input X, line N returns Y, which is wrong \
because Z" beats "this could be fragile".

You MUST end your reply with a status block in exactly this form, and nothing after it:

STATUS: FINDINGS <n>
or
STATUS: NO FINDINGS

Before the status block, list each finding as:

FINDING <severity: HIGH|MEDIUM|LOW> <file:line or symbol>
<one paragraph: what is wrong, and the input that demonstrates it>
"""

_STATUS_RE = re.compile(r"^STATUS:\s*(NO FINDINGS|FINDINGS\s+(\d+))\s*$", re.M | re.I)
_FINDING_RE = re.compile(
    r"^FINDING\s+(HIGH|MEDIUM|LOW)\s*(.*)$(.*?)(?=^FINDING\s|^STATUS:|\Z)", re.M | re.I | re.S
)


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
        header = f.split("\n@@", 1)[0]
        hunks = re.split(r"(?m)^(?=@@ )", f[len(header) :])
        current = header
        for h in hunks:
            if current != header and len(current) + len(h) > max_chars:
                pieces.append(current)
                current = header
            current += h
        if current.strip() != header.strip():
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


def _chat(rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    """One chat completion. Returns (content, error). Never raises."""
    payload: dict[str, Any] = {
        "model": rev.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": rev.temperature,
        "max_tokens": rev.max_tokens,
        **rev.extra_body,
    }
    req = urllib.request.Request(
        rev.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {rev.api_key()}"} if rev.api_key() else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return "", f"HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}"
    except (urllib.error.URLError, OSError) as exc:
        return "", f"unreachable: {exc}"
    except (ValueError, json.JSONDecodeError) as exc:
        return "", f"bad response body: {exc}"
    try:
        choice = body["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError, TypeError):
        return "", f"unexpected response shape: {str(body)[:300]}"
    content = (msg.get("content") or "").strip()
    if not content and choice.get("finish_reason") == "length":
        # Naming the remedy matters: "empty completion" sends an operator hunting for a
        # broken endpoint, when the endpoint is fine and the token budget is too small
        # for a model that thinks before it answers.
        used = (body.get("usage") or {}).get("completion_tokens_details") or {}
        return "", (
            f"TRUNCATED: the model consumed all {rev.max_tokens} tokens "
            f"({used.get('reasoning_tokens', '?')} of them reasoning) before emitting "
            f"any answer. Raise [[reviewer]].max_tokens or lower max_chunk_chars."
        )
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
            title=(m.group(2) or "").strip() or (m.group(3) or "").strip()[:80],
            detail=(m.group(3) or "").strip(),
        )
        for m in _FINDING_RE.finditer(text or "")
    ]
    return findings, status is not None


def review(
    rev: Reviewer, diff: str, intent: str, *, context: str = "", on_chunk=None
) -> ReviewResult:
    """Run one reviewer over one diff. Never raises; encodes everything in the status."""
    res = ReviewResult(reviewer=rev.name, model=rev.model, family=rev.resolved_family())
    started = time.time()

    if not rev.enabled:
        res.status, res.reason = UNAVAILABLE, "reviewer is disabled in config"
        return res
    if not rev.base_url or not rev.model:
        res.status, res.reason = UNAVAILABLE, "reviewer has no base_url or model"
        return res
    if not diff.strip():
        # An empty diff is the commonest way a review passes vacuously: the command ran,
        # the model replied "looks fine", and nothing was actually examined.
        res.status, res.reason = UNAVAILABLE, "the diff is empty — nothing was reviewed"
        return res

    chunks = split_diff(diff, rev.max_chunk_chars)
    res.chunks_total = len(chunks)
    system = _SYSTEM
    if rev.system_prompt_path:
        path = Path(rev.system_prompt_path)
        if path.is_file():
            system = path.read_text("utf-8")
        else:
            res.status, res.reason = ERROR, f"system_prompt_path not found: {path}"
            return res

    all_raw: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        remaining = rev.total_budget_s - (time.time() - started)
        if remaining <= 0:
            res.reason = (
                f"total budget {rev.total_budget_s}s exhausted after {i - 1}/{len(chunks)} chunk(s)"
            )
            break
        user = (
            f"## Intent of this change\n\n{intent}\n\n"
            + (f"## Context\n\n{context}\n\n" if context else "")
            + f"## Diff (part {i} of {len(chunks)})\n\n```diff\n{chunk}\n```\n"
        )
        content, err = _chat(rev, system, user, min(rev.timeout_s, remaining))
        if err:
            res.reason = res.reason or f"chunk {i}/{len(chunks)}: {err}"
            continue
        if not content:
            res.chunks_off_contract += 1
            res.reason = res.reason or f"chunk {i}: empty completion"
            continue
        all_raw.append(content)
        findings, on_contract = parse(content)
        if not on_contract:
            # Length is not a verdict. A reasoning model can emit thousands of tokens
            # of deliberation with no conclusion in it; counting that as a clean review
            # is the vacuous pass at the level of a whole reviewer.
            res.chunks_off_contract += 1
            res.reason = res.reason or (
                f"chunk {i} came back OFF CONTRACT: {len(content)} chars with no "
                f"STATUS: block. Not counted as reviewed."
            )
            continue
        res.chunks_reviewed += 1
        res.findings.extend(findings)
        if on_chunk:
            on_chunk(i, len(chunks), findings)

    res.raw = "\n\n---\n\n".join(all_raw)
    res.elapsed_s = time.time() - started
    if res.chunks_reviewed == 0:
        res.status = UNAVAILABLE
        res.reason = res.reason or "no chunk produced a usable review"
    elif res.chunks_reviewed < res.chunks_total:
        res.status = PARTIAL
    else:
        res.status = REVIEWED
        res.reason = ""
    return res
