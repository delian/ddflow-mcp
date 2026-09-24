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
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import proc as P
from .config import family_for
from .gates import _missing_executable

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


def family_of(model: str) -> str:
    """Delegates to `config.family_for`. Kept as a name because it reads better at the
    call sites here, and because `Reviewer.resolved_family` is the natural home for the
    "declared family wins over guessed family" rule."""
    return family_for(model)


#: Ready-made settings for the providers people actually use. `orchard reviewers add
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
    #: Extra environment for a kind="command" reviewer (e.g. a per-reviewer API key).
    env: dict[str, str] = field(default_factory=dict)

    def resolved_family(self) -> str:
        """Declared family wins over guessed. ``""`` means nobody has classified this
        model — `orchard reviewers list` flags it, because an unclassified reviewer
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
    from . import tomlcfg
    from .config import Config

    blocks = tomlcfg.overlay_array(
        tomlcfg.config_paths(root, "reviewers.toml"),
        "reviewer",
        Reviewer,
        key="name",
        fallback_key="model",
    )
    # The project's own family map, not just the shipped one. `resolved_family` had no
    # Config in scope, so it always used `FAMILY_HINTS` while `reviewer_independence`
    # used `[agent].families` — a project that taught the map its in-house model name
    # got it honoured by the check that decides whether a review counted and ignored by
    # `orchard reviewers list` and by the family recorded on the result. Two surfaces,
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


def probe_endpoint(base_url: str, timeout_s: float = 4.0) -> list[str]:
    """Model ids served at ``base_url``, or [] if nothing answers.

    Deliberately short-timeout and exception-swallowing: this runs against a list of
    candidate ports, and a closed port must cost milliseconds, not a stack trace.
    """
    from .container import rewrite_localhost

    url = rewrite_localhost(base_url).rstrip("/") + "/models"
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

#: Kept ONLY as a last-resort fallback for a stripped deployment where the packaged
#: templates are unreadable. The canonical text lives in
#: `orchard/templates/prompts/review_system.md` and is what operators edit.
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
    missing = _missing_executable(cmd)
    if missing:
        return "", (
            f"executable {missing!r} is not on PATH -- the reviewer could not run. "
            f"This is NOT a clean review."
        )
    try:
        p = P.run(
            cmd,
            shell=True,
            input=f"{system}\n\n{user}",
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={**os.environ, **rev.env},
        )
    except subprocess.TimeoutExpired:
        return "", f"command timed out after {timeout_s:.0f}s"
    except (OSError, ValueError) as exc:
        return "", f"could not execute: {exc}"
    out = (p.stdout or "").strip()
    if p.returncode != 0:
        return "", (f"command exited {p.returncode}: {((p.stderr or '') + out).strip()[:300]}")
    if not out:
        return "", "command produced no output on stdout"
    return out, ""


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
    from .container import rewrite_localhost

    req = urllib.request.Request(
        rewrite_localhost(url),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), ""
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}"
    except (urllib.error.URLError, OSError) as exc:
        return None, f"unreachable: {exc}"
    except (ValueError, json.JSONDecodeError) as exc:
        return None, f"bad response body: {exc}"


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
            "temperature": rev.temperature,
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
    if not text and body.get("stop_reason") == "max_tokens":
        return "", (
            f"TRUNCATED: hit max_tokens ({rev.max_tokens}) before emitting an "
            f"answer. Raise [[reviewer]].max_tokens or lower max_chunk_chars."
        )
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
            "generationConfig": {"temperature": rev.temperature, "maxOutputTokens": rev.max_tokens},
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
    if not text and cand.get("finishReason") == "MAX_TOKENS":
        return "", (
            f"TRUNCATED: hit maxOutputTokens ({rev.max_tokens}) before emitting "
            f"an answer. Raise [[reviewer]].max_tokens."
        )
    return text, ""


def _chat_openai(rev: Reviewer, system: str, user: str, timeout_s: float) -> tuple[str, str]:
    payload: dict[str, Any] = {
        "model": rev.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": rev.temperature,
        "max_tokens": rev.max_tokens,
        **rev.extra_body,
    }
    from .container import rewrite_localhost

    req = urllib.request.Request(
        rewrite_localhost(rev.base_url).rstrip("/") + "/chat/completions",
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


def _absorb_chunk(
    res: ReviewResult, index: int, total: int, content: str, err: str, all_raw: list[str]
) -> list[Finding] | None:
    """Fold one chunk's reply into the result. Returns its findings, or None if it did
    not produce a usable review.

    The three not-a-review cases are kept distinct because their remedies differ:
    a transport error (endpoint, key, binary), an EMPTY completion, and an
    OFF-CONTRACT reply — thousands of tokens of deliberation with no STATUS block in
    them. Only the last is easy to mistake for a review, which is why length is never
    treated as a verdict.
    """
    if err:
        res.reason = res.reason or f"chunk {index}/{total}: {err}"
        return None
    if not content:
        res.chunks_off_contract += 1
        res.reason = res.reason or f"chunk {index}: empty completion"
        return None
    all_raw.append(content)
    findings, on_contract = parse(content)
    if not on_contract:
        res.chunks_off_contract += 1
        res.reason = res.reason or (
            f"chunk {index} came back OFF CONTRACT: {len(content)} chars with no "
            f"STATUS: block. Not counted as reviewed."
        )
        return None
    res.chunks_reviewed += 1
    res.findings.extend(findings)
    return findings


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
            shell=True,
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
    from .container import rewrite_localhost

    try:
        with urllib.request.urlopen(rewrite_localhost(url), timeout=timeout_s) as r:
            return _HTTP_OK_FLOOR <= r.status < _HTTP_SERVER_ERROR
    except urllib.error.HTTPError as exc:
        return exc.code < _HTTP_SERVER_ERROR
    except (urllib.error.URLError, OSError):
        return False


def review(
    rev: Reviewer,
    diff: str,
    intent: str,
    *,
    context: str = "",
    on_chunk=None,
    repo: Path | None = None,
    prompt_overrides: dict[str, str] | None = None,
    extra_rules: str = "",
) -> ReviewResult:
    """Run one reviewer over one diff. Never raises; encodes everything in the status."""
    res = ReviewResult(reviewer=rev.name, model=rev.model, family=rev.resolved_family())
    started = time.time()

    problem = _preflight(rev, diff, res)
    if problem is not None:
        return problem

    chunks = split_diff(diff, rev.max_chunk_chars)
    res.chunks_total = len(chunks)

    from . import prompts as P

    overrides = dict(prompt_overrides or {})
    if rev.system_prompt_path:
        # A per-reviewer override still wins: two reviewers may want different
        # instructions, which a single project-wide template cannot express.
        overrides["review_system"] = rev.system_prompt_path
    try:
        system = P.render(P.resolve("review_system", repo, overrides), extra_rules=extra_rules)
        user_tmpl = P.resolve("review_user", repo, overrides)
    except P.TemplateError as exc:
        res.status, res.reason = ERROR, str(exc)
        return res

    all_raw: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        remaining = rev.total_budget_s - (time.time() - started)
        if remaining <= 0:
            res.reason = (
                f"total budget {rev.total_budget_s}s exhausted after {i - 1}/{len(chunks)} chunk(s)"
            )
            break
        try:
            user = P.render(
                user_tmpl,
                intent=intent,
                context=context,
                diff=chunk,
                chunk_index=i,
                chunk_total=len(chunks),
            )
        except P.TemplateError as exc:
            res.status, res.reason = ERROR, f"review_user template: {exc}"
            return res

        content, err = _chat(rev, system, user, min(rev.timeout_s, remaining))
        note = _absorb_chunk(res, i, len(chunks), content, err, all_raw)
        if note is not None and on_chunk:
            on_chunk(i, len(chunks), note)

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
