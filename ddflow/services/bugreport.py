"""The sanitized, reproducible bundle behind an upstream bug report.

Decision D-upstream-reporting (3): ddflow builds the report itself from an allowlist of
fields; the agent supplies only a short title and "what I expected". This module is the
pure builder. It reads nothing from the machine except through arguments (so a test hands
it fixtures) and it sends nothing anywhere: delivery and consent are other modules'.

What goes in: ddflow's version, how it was installed and from which commit; python and
OS (never the host name); the command's argv with every value removed and its exit code
and class; stderr and traceback with ddflow-relative frames only; an explicit allowlist of
configuration knobs; about twenty recent ddflow event KINDS with agent ids replaced by
stable aliases (`a-1`, `a-2`, ...); provenance; local duplicate candidates; and the agent's
title and expectation. What never goes in: event subjects or payloads, item titles or
bodies, prompts, paths, project or repo names, endpoints, or any config value outside
`KNOB_ALLOWLIST` -- and every string that is left passes through `redact_report`, whose
per-kind counts are recorded in the bundle.

The digest is a sha256 over the exact bytes of both renderings (markdown body, then the
canonical JSON), so a consent bound to it covers exactly what would be sent: change one
byte and `verify_digest` says no.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.events import Event
from ..core.model import known_kinds
from . import install_info as _install
from . import redact_report as _redact

#: The only configuration values a report may carry: scalar behaviour switches and
#: limits, nothing that can hold a path, an endpoint, a name, a prompt or a pattern.
KNOB_ALLOWLIST: tuple[str, ...] = (
    "lease.ttl_s",
    "lease.heartbeat_s",
    "lease.reclaim_policy",
    "worktree.enabled",
    "worktree.merge_strategy",
    "worktree.max_parallel",
    "flow.model",
    "flow.integration",
    "flow.forge",
    "flow.pr_merge",
    "gates.enforce_order",
    "gates.unavailable_is_failure",
    "gates.allow_skip_with_reason",
    "gates.require_outcome",
    "dedupe.on_match",
    "dedupe.show_floor",
    "dedupe.ask_threshold",
    "dedupe.max_candidates",
    "dedupe.min_words",
    "enforce.commit_without_lease",
    "enforce.stale_rules",
    "enforce.generated_views",
    "enforce.stale_docs",
    "enforce.readme_with_code",
    "enforce.behind",
    "loops.on_detect",
    "schedule.ready_policy",
    "session.log_prompts",
    "agent.reviewer_family_must_differ",
)

EVENT_EXCERPT = 20
TITLE_MAX = 120
EXPECTED_MAX = 1000
STDERR_MAX = 4000
TRACEBACK_MAX = 6000
UNKNOWN_KIND = "(unknown)"
_CUT = " [truncated]"
_SURROGATES = re.compile("[\ud800-\udfff]")

_DETECTOR = re.compile(r"[a-z][a-z0-9_.-]{0,40}")
_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,80}")
_PROGRAMS = frozenset({"ddflow", "ddflow-mcp", "python", "python3", "uv", "uvx"})
_WORD = re.compile(r"[a-z][a-z0-9_-]{0,23}")
_FILE_LINE = re.compile(r'^(\s*File ")([^"]+)(".*)$')
_ANY_DDFLOW_PATH = re.compile(r"(?:/[\w.@+~-]+)+/ddflow/")


@dataclass(frozen=True)
class Provenance:
    """Who reported it and how it was noticed. `detector` is the name of a mechanical
    detector or `agent-judgement`."""

    reported_by_agent: str
    on_behalf_of_operator: str = ""
    session: str = ""
    detector: str = "agent-judgement"


@dataclass(frozen=True)
class Failure:
    """The command that went wrong. `argv` is raw; the bundle keeps only its shape."""

    argv: Sequence[str] = ()
    exit_code: int | None = None
    error_class: str = ""
    stderr: str = ""
    traceback: str = ""
    #: The CLI's own verbs (its parser's choices): the words of `argv` that may stay.
    subcommands: Iterable[str] | None = None


@dataclass(frozen=True)
class Scrub:
    """What `redact_report` is told about this machine and project."""

    hostname: str | None = None
    names: Sequence[str] = ()
    home: str | None = None
    repo_root: str | None = None
    cfg: Config | None = None


@dataclass(frozen=True)
class Bundle:
    data: dict[str, Any]
    body: str  # the markdown without its digest trailer
    json: str  # canonical JSON of `data` plus the digest
    digest: str
    redactions: Mapping[str, int] = field(default_factory=dict)

    @property
    def markdown(self) -> str:
        return self.body

    @property
    def rendered(self) -> str:
        return self.body + _trailer(self.digest)

    def verify(self) -> bool:
        return verify_digest(self.rendered, self.json)


# ------------------------------------------------------------------------- knobs


def knobs_from_config(cfg: Config) -> dict[str, Any]:
    """The allowlisted knobs of `cfg`, by dotted name."""
    out: dict[str, Any] = {}
    for dotted in KNOB_ALLOWLIST:
        section, _, name = dotted.partition(".")
        out[dotted] = getattr(getattr(cfg, section), name)
    return out


# ------------------------------------------------------------------- traceback


def normalise_traceback(text: str) -> str:
    """`text` with every frame path made ddflow-relative.

    A frame inside ddflow becomes `ddflow/<module path>` (its source line is public code
    and stays); a frame in site-packages or the standard library names the library; any
    other frame keeps only its file's base name and loses its source line, which is the
    operator's own code. Paths elsewhere in the text that run through a `ddflow/`
    directory are shortened the same way."""
    out: list[str] = []
    drop_source = False
    for line in str(text or "").splitlines():
        m = _FILE_LINE.match(line)
        if m:
            path, own = _frame_path(m.group(2))
            drop_source = not own
            out.append(f"{m.group(1)}{path}{m.group(3)}")
            continue
        if drop_source and line.startswith((" ", "\t")):
            continue  # the source line (and caret line) of a frame outside ddflow
        drop_source = False
        out.append(_ANY_DDFLOW_PATH.sub("ddflow/", line))
    return "\n".join(out)


def _frame_path(path: str) -> tuple[str, bool]:
    norm = path.replace("\\", "/")
    if norm.startswith("<"):
        return norm, True  # <frozen ...>, <string>: not a path
    marker = "/ddflow/"
    if marker in norm:
        return "ddflow/" + norm.rsplit(marker, 1)[1], True
    for lib, label in (("site-packages", "<site-packages>"), ("dist-packages", "<site-packages>")):
        if f"/{lib}/" in norm:
            return f"{label}/" + norm.rsplit(f"/{lib}/", 1)[1], False
    stdlib = re.search(r"/lib/python[\d.]+/(.*)$", norm)
    if stdlib:
        return "<stdlib>/" + stdlib.group(1), False
    return "<path>/" + norm.rsplit("/", 1)[-1], False


# ------------------------------------------------------------------------- argv


def redact_argv(argv: Sequence[str], subcommands: Iterable[str] | None = None) -> list[str]:
    """The shape of a command line: the program (when it is ddflow or a Python
    launcher), the leading subcommand words the caller lists in `subcommands` (the CLI's
    own verbs), every flag name, and `<value>` / `<arg>` in place of anything else. With
    no `subcommands` nothing a person could have typed survives."""
    allowed = set(subcommands) if subcommands is not None else set()
    out: list[str] = []
    leading = True
    for i, raw in enumerate(argv):
        tok = str(raw)
        if i == 0:
            base = tok.replace("\\", "/").rsplit("/", 1)[-1]
            out.append(base if base in _PROGRAMS else "<program>")
        elif tok.startswith("-") and tok != "-":
            leading = False
            name, eq, _value = tok.partition("=")
            out.append(f"{name}=<value>" if eq else name)
        elif tok in _PROGRAMS and out[-1] == "-m":
            out.append(tok)  # `python -m ddflow`
        elif leading and _WORD.fullmatch(tok) and tok in allowed:
            out.append(tok)
        else:
            leading = False
            after_flag = out[-1].startswith("-") and "=" not in out[-1]
            out.append("<value>" if after_flag else "<arg>")
    return out


# --------------------------------------------------------------------- aliases


class _Aliases:
    """Stable aliases by first appearance: the same input always gives the same ones."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.seen: dict[str, str] = {}

    def __call__(self, raw: str) -> str:
        key = str(raw or "")
        if not key:
            return ""
        if key not in self.seen:
            self.seen[key] = f"{self.prefix}-{len(self.seen) + 1}"
        return self.seen[key]


# --------------------------------------------------------------------- builder


def own_dev_tree(root: Path | None = None) -> bool:
    """True when `root` is ddflow's own source tree (see `install_info.is_own_dev_tree`)."""
    return _install.is_own_dev_tree(root)


def local_candidates(repo: Path, cfg: Config, log) -> Callable[[str], list[dict[str, Any]]]:
    """A candidates provider over this project's own log: the records `services.similar`
    scores closest to a title, as `{id, kind, score}`. Raises `LookupError` or `OSError`
    when the index cannot answer; `build_bundle` reports that as unavailable."""

    def provider(title: str) -> list[dict[str, Any]]:
        from ..infra.store import Store
        from . import similar as sim

        kinds = list(cfg.dedupe.kinds)
        scoped = dataclasses.replace(
            cfg, dedupe=dataclasses.replace(cfg.dedupe, on_match="ask", kinds=kinds)
        )
        store = Store(repo, cfg)
        store.ensure(log)
        with sim.open_store(store) as matcher:
            found = sim.assess(matcher, {"kind": kinds[0], "title": title, "body": ""}, scoped)
        return [{"id": c.id, "kind": c.kind, "score": c.score} for c in found.candidates]

    return provider


def build_bundle(
    *,
    title: str,
    expected: str,
    install: _install.InstallInfo,
    provenance: Provenance,
    failure: Failure | None = None,
    events: Iterable[Event] = (),
    knobs: Mapping[str, Any] | None = None,
    candidates: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
    env: Mapping[str, str] | None = None,
    scrub: Scrub | None = None,
) -> Bundle:
    """Build the bundle. Every string in it has been through `redact_report`; the count
    per kind is in `Bundle.redactions` and in the data itself."""
    scrub = scrub or Scrub()
    if not _DETECTOR.fullmatch(provenance.detector or ""):
        raise ValueError(
            f"detector {provenance.detector!r} must be `agent-judgement` or a lowercase name"
        )
    counts: dict[str, int] = {}

    def clean(value: object) -> str:
        r = _redact.redact_report(
            value,
            hostname=scrub.hostname,
            names=scrub.names,
            home=scrub.home,
            repo_root=scrub.repo_root,
            cfg=scrub.cfg,
        )
        for kind, n in r.counts.items():
            counts[kind] = counts.get(kind, 0) + n
        return _SURROGATES.sub("\ufffd", r.text)

    def bounded(value: object, limit: int, *, one_line: bool = False) -> str:
        text = clean(value)
        if one_line:
            text = " ".join(text.split())
        if len(text) > limit:
            text = clean(text[: limit - len(_CUT)]) + _CUT
        return text

    agents = _Aliases("a")
    reporter = agents(provenance.reported_by_agent)
    operator = "op-1" if provenance.on_behalf_of_operator else ""
    session = (
        "s-" + hashlib.sha256(provenance.session.encode("utf-8", "replace")).hexdigest()[:8]
        if provenance.session
        else ""
    )

    title_text = bounded(title, TITLE_MAX, one_line=True)
    data: dict[str, Any] = {
        "title": title_text,
        "expected": bounded(expected, EXPECTED_MAX),
        "install": {
            "version": clean(install.version),
            "kind": clean(install.kind),
            "commit": clean(install.commit or ""),
            "own_dev_tree": bool(install.is_own_dev_tree),
        },
        "environment": {k: clean(v) for k, v in sorted((env or {}).items())},
        "command": _command(failure, clean),
        "knobs": {
            k: _scalar(v, clean) for k, v in sorted((knobs or {}).items()) if k in KNOB_ALLOWLIST
        },
        "recent_events": _excerpt(events, agents),
        "provenance": {
            "reported_by_agent": reporter,
            "on_behalf_of_operator": operator,
            "session": session,
            "detector": provenance.detector,
        },
        "dedupe_candidates": _candidates(candidates, title_text, clean),
    }
    data["redactions"] = dict(sorted(counts.items()))
    body = render_markdown(data)
    json_body = render_json(data)
    digest = digest_of(body, json_body)
    return Bundle(
        data=data,
        body=body,
        json=render_json({**data, "digest": digest}),
        digest=digest,
        redactions=dict(sorted(counts.items())),
    )


def _scalar(value: Any, clean: Callable[[object], str]) -> Any:
    return value if isinstance(value, (bool, int, float)) else clean(value)


def _command(failure: Failure | None, clean: Callable[[object], str]) -> dict[str, Any]:
    if failure is None:
        return {}
    cls = failure.error_class if _CLASS.fullmatch(failure.error_class or "") else ""
    out: dict[str, Any] = {
        "argv": [clean(t) for t in redact_argv(failure.argv, failure.subcommands)],
        "exit_code": failure.exit_code,
        "error_class": clean(cls),
    }
    if failure.stderr:
        out["stderr"] = _tail(clean(normalise_traceback(failure.stderr)), STDERR_MAX)
    if failure.traceback:
        out["traceback"] = _tail(clean(normalise_traceback(failure.traceback)), TRACEBACK_MAX)
    return out


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "[...]\n" + text[-limit:]


def _excerpt(events: Iterable[Event], agents: _Aliases) -> list[dict[str, Any]]:
    known = known_kinds()
    recent = list(events)[-EVENT_EXCERPT:]
    return [
        {
            "n": i + 1,
            "kind": e.kind if e.kind in known else UNKNOWN_KIND,
            "agent": agents(e.agent),
        }
        for i, e in enumerate(recent)
    ]


def _candidates(
    provider: Callable[[str], Sequence[Mapping[str, Any]]] | None,
    title: str,
    clean: Callable[[object], str],
) -> dict[str, Any]:
    if provider is None:
        return {"status": "not_checked", "items": []}
    try:
        items = [
            {
                "id": clean(r.get("id", "")),
                "kind": clean(r.get("kind", "")),
                "score": round(float(r.get("score", 0.0)), 2),
            }
            for r in provider(title)
        ]
    except Exception as exc:  # an unavailable or malformed index is said, never read as "none"
        return {"status": "unavailable", "reason": clean(type(exc).__name__), "items": []}
    return {"status": "ok" if items else "none", "items": items}


# ------------------------------------------------------------------- rendering


def render_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def render_markdown(data: Mapping[str, Any]) -> str:
    inst = data["install"]
    cmd = data["command"]
    prov = data["provenance"]
    cands = data["dedupe_candidates"]
    lines = [
        f"# {data['title']}",
        "",
        "## What I expected",
        "",
        data["expected"] or "(not given)",
        "",
        "## ddflow",
        "",
        f"- version: {inst['version']}",
        f"- install: {inst['kind']}" + (f" at {inst['commit']}" if inst["commit"] else ""),
        f"- own dev tree: {'yes' if inst['own_dev_tree'] else 'no'}",
    ]
    lines += [f"- {k}: {v}" for k, v in data["environment"].items()]
    if cmd:
        lines += ["", "## Command", "", f"    {' '.join(cmd['argv'])}", ""]
        lines += [f"- exit code: {cmd['exit_code']}", f"- class: {cmd['error_class'] or '(none)'}"]
        for key in ("stderr", "traceback"):
            if cmd.get(key):
                lines += ["", f"### {key}", "", "```", cmd[key], "```"]
    lines += ["", "## Knobs", ""]
    lines += [f"- {k} = {v}" for k, v in data["knobs"].items()] or ["(none)"]
    lines += ["", f"## Recent events (last {len(data['recent_events'])})", ""]
    lines += [f"{e['n']}. {e['kind']} by {e['agent'] or '-'}" for e in data["recent_events"]]
    lines += [
        "",
        "## Provenance",
        "",
        f"- reported by: {prov['reported_by_agent'] or '-'}",
        f"- on behalf of: {prov['on_behalf_of_operator'] or '-'}",
        f"- session: {prov['session'] or '-'}",
        f"- detector: {prov['detector']}",
        "",
        "## Local duplicate candidates",
        "",
    ]
    if cands["status"] == "unavailable":
        lines.append(
            f"unavailable: the local similarity index could not answer ({cands['reason']})"
        )
    elif cands["status"] == "not_checked":
        lines.append("not checked")
    elif not cands["items"]:
        lines.append("none")
    else:
        lines += [f"- {c['id']} ({c['kind']}) score {c['score']}" for c in cands["items"]]
    red = data["redactions"]
    lines += ["", "## Redactions", ""]
    lines += [f"- {k}: {v}" for k, v in red.items()] or ["none"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- digest


def digest_of(body: str, json_body: str) -> str:
    """sha256 over the exact bytes of the markdown body and the canonical JSON body."""
    h = hashlib.sha256()
    h.update(body.encode("utf-8"))
    h.update(b"\n--json--\n")
    h.update(json_body.encode("utf-8"))
    return h.hexdigest()


def _trailer(digest: str) -> str:
    return f"\n---\ndigest: sha256:{digest}\n"


_TRAILER = re.compile(r"\n---\ndigest: sha256:([0-9a-f]{64})\n\Z")


def verify_digest(rendered: str, json_text: str) -> bool:
    """True when `rendered` (markdown with its trailer) and `json_text` are exactly what
    the digest in both was computed over."""
    m = _TRAILER.search(rendered)
    if not m:
        return False
    try:
        data = json.loads(json_text)
    except ValueError:
        return False
    if not isinstance(data, dict) or data.get("digest") != m.group(1):
        return False
    body = rendered[: m.start()]
    bare = {k: v for k, v in data.items() if k != "digest"}
    return digest_of(body, render_json(bare)) == m.group(1) and json_text == render_json(data)


__all__ = [
    "EVENT_EXCERPT",
    "KNOB_ALLOWLIST",
    "Bundle",
    "Failure",
    "Provenance",
    "Scrub",
    "build_bundle",
    "digest_of",
    "knobs_from_config",
    "known_kinds",
    "local_candidates",
    "normalise_traceback",
    "own_dev_tree",
    "redact_argv",
    "render_json",
    "render_markdown",
    "verify_digest",
]
