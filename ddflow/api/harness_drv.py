"""`ddflow harness add | lint | test | probe` (DRV-cli): author a descriptor, never guess.

* ``add``   -- a skeleton with every fact left out and listed under `[verified].unverified`;
* ``lint``  -- the loader's checks plus the three-valued rule, every problem at once, an
  unknown key named by section and key;
* ``test``  -- the offline conformance of ONE descriptor, through the interface DRV-conformance
  exposes (`ddflow.services.driver_conformance.run`); until it lands the answer is `unavailable`;
* ``probe`` -- what the agent's binary would be asked (`--version` only). It runs NOTHING
  unless the operator passes ``run`` (CLI `--run`); over MCP it never runs. It writes no fact.

Output goes through the network redaction: no endpoint address leaves this module.
"""

from __future__ import annotations

import importlib
import json
import re
import tomllib
from dataclasses import fields
from datetime import date
from pathlib import Path
from typing import Any

from ..core import clock
from ..core import outcome as O
from ..core.redact import redact_network
from ..infra import fsio
from ..services import harnessreg as HR
from ..services import harnessv2 as H2
from ..services.engines import probe as EP
from ..services.engines.safety import EngineError

LOCAL_SUBDIR = Path(".ddflow") / "local" / "harnesses"
CONFORMANCE_MODULE = "ddflow.services.driver_conformance"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
PLACEHOLDER = "unverified"
RANK_STEP = 10
_FACT_SECTIONS = (
    "mcp",
    "instructions",
    "hooks",
    "plugin",
    "commands",
    "skills",
    "subagents",
    "env",
    "worktrees",
    *H2.V2_SECTIONS,
)
_TOP = ("id", "rank", "family", "delta", "title", "facts")
_KNOWN_TABLES = {
    **HR._SECTIONS,
    "mcp": HR.Mcp,
    "verified": HR.Verified,
}


def _clean(text: str) -> str:
    return redact_network(text)


def _q(text: str) -> str:
    """A TOML basic string literal, whatever the text holds (quotes, backslashes, newlines)."""
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def skeleton(hid: str, rank: int, family: str, title: str) -> str:
    left = ", ".join(f'"{s}"' for s in _FACT_SECTIONS)
    return (
        f"# {hid}: skeleton from `ddflow harness add`. Every fact is LEFT OUT (not verified).\n"
        "# Add a section only with a value or `none = true` you checked; cite it in [verified].\n"
        "# `ddflow harness lint` says what is missing; adding-a-harness.md has the worked example.\n"
        f"id = {_q(hid)}\nrank = {rank}\nfamily = {_q(family)}\n"
        f"delta = {_q(hid + '.md')}\ntitle = {_q(title)}\n\n"
        "# [mcp]  <- required: path and shape\n\n"
        f'[verified]\ndate = "{date.today().isoformat()}"\nsources = []\n'
        f"unverified = [{left}]\n"
    )


def _local_dir(repo: Path) -> Path:
    return Path(repo) / LOCAL_SUBDIR


def _find(repo: Path, target: str) -> Path | None:
    if target.endswith(".toml") or "/" in target:
        p = Path(target)
        return p if p.is_absolute() else Path(repo) / p
    for d in (_local_dir(repo), HR.HARNESS_DIR):
        if (d / f"{target}.toml").is_file():
            return d / f"{target}.toml"
    return None


def harness_add(
    repo: Path, hid: str, family: str = "", title: str = "", directory: str = ""
) -> O.Outcome:
    if not ID_RE.match(hid or ""):
        return O.failed("harness_add", f"id {hid!r}: lowercase letters, digits, - and _ only")
    if HR.get(hid) is not None:
        return O.failed("harness_add", f"{hid!r} is already a shipped harness")
    out_dir = Path(directory) if directory else _local_dir(repo)
    path = out_dir / f"{hid}.toml"
    if path.exists():
        return O.failed("harness_add", f"{_clean(str(path))} exists; it is never overwritten")
    rank = max((h.rank for h in HR.load()), default=0) + RANK_STEP
    text = skeleton(hid, rank, family or PLACEHOLDER, title or hid)
    fsio.atomic_write(path, text)
    msg = f"wrote {path}: every fact left out; next: ddflow harness lint {hid}"
    return O.ok("harness_add", id=hid, file=str(path), message=_clean(msg))


def _unknown_keys(raw: dict[str, Any]) -> list[str]:
    known_top = set(_TOP) | set(_KNOWN_TABLES) | set(H2.RESERVED_SECTIONS)
    out = [f"unknown top-level key {k!r}" for k in sorted(set(raw) - known_top)]
    for name, cls in _KNOWN_TABLES.items():
        node = raw.get(name)
        if isinstance(node, dict):
            names = {f.name for f in fields(cls)}
            out += [f"[{name}] unknown key {k!r}" for k in sorted(set(node) - names)]
    return out


def _three_valued(raw: dict[str, Any]) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warns: list[str] = []
    ver = raw.get("verified")
    got = ver.get("unverified") if isinstance(ver, dict) else None
    listed = {x for x in got if isinstance(x, str)} if isinstance(got, list) else set()
    for s in _FACT_SECTIONS:
        node = raw.get(s)
        if isinstance(node, dict) and node.get("none") is True and s in listed:
            problems.append(f"[{s}] says none = true but [verified].unverified lists it")
        if node is None and s not in listed:
            warns.append(f"[{s}] is left out but not listed under [verified].unverified")
    if raw.get("family") == PLACEHOLDER:
        warns.append(f"family is the placeholder {PLACEHOLDER!r}")
    return problems, warns


def harness_lint(repo: Path, target: str) -> O.Outcome:
    path = _find(repo, target or "")
    if path is None or not path.is_file():
        return O.failed("harness_lint", _clean(f"no descriptor {target!r} (local or shipped)"))
    text = path.read_text(encoding="utf-8")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return O.failed("harness_lint", _clean(f"{path.name}: {exc}"), problems=[str(exc)])
    problems = _unknown_keys(raw)
    extra, warns = _three_valued(raw)
    problems += extra
    if "mcp" not in raw:
        problems.append("[mcp] is required (path and shape)")
    try:
        HR.parse(text, path.stem)
    except H2.DescriptorError as exc:
        problems.append(str(exc))
    problems = [_clean(p) for p in problems]
    lines = [f"problem: {p}" for p in problems] + [f"warning: {w}" for w in warns]
    msg = "\n".join(lines) or f"{path.stem}: lints clean"
    data = {"id": path.stem, "problems": problems, "warnings": warns, "message": msg}
    if problems:
        return O.Outcome(
            kind="harness_lint", data=data, exit=O.FAIL, reason=f"{len(problems)} problem(s)"
        )
    return O.ok("harness_lint", **data)


def _load(repo: Path, target: str) -> tuple[HR.Harness | None, str]:
    path = _find(repo, target or "")
    if path is None or not path.is_file():
        return None, _clean(f"no descriptor {target!r} (local or shipped)")
    try:
        return HR.parse(path.read_text(encoding="utf-8"), path.stem), ""
    except H2.DescriptorError as exc:
        return None, _clean(str(exc))


def harness_test(repo: Path, target: str) -> O.Outcome:
    h, err = _load(repo, target)
    if h is None:
        return O.failed("harness_test", err)
    try:
        suite = importlib.import_module(CONFORMANCE_MODULE)
    except ImportError:
        why = f"offline conformance is unavailable: {CONFORMANCE_MODULE} has not landed (DRV-conformance)"
        return O.nothing("harness_test", why, id=h.id, status="unavailable", message=why)
    res = suite.run(h)
    return O.ok("harness_test", id=h.id, status="ran", result=res, message=_clean(str(res)))


def harness_probe(repo: Path, target: str, run: bool = False) -> O.Outcome:
    h, err = _load(repo, target)
    if h is None:
        return O.failed("harness_probe", err)
    names = h.detect.process_names if h.detect else ()
    if not names:
        why = "no [detect].process_names: no binary is named, nothing to run (not guessed)"
        return O.nothing("harness_probe", why, id=h.id, status="unavailable", message=why)
    argvs = [[n, "--version"] for n in names]
    try:
        EP.parse(argvs)
    except EngineError as exc:
        return O.failed("harness_probe", _clean(str(exc)))
    would = "; ".join(" ".join(a) for a in argvs)
    if not run:
        why = f"not run, the operator has not consented (CLI --run). It would run: {would}"
        facts = [
            {"argv": a, "status": "unavailable", "reason": "not run: no consent"} for a in argvs
        ]
        return O.nothing(
            "harness_probe", why, id=h.id, status="unavailable", facts=facts, message=why
        )
    got = EP.run(argvs, probe_id=f"cli:{h.id}", now=clock.now_iso())
    keep = ("argv", "status", "reason", "exit_code", "tool_version")
    facts = [{k: f[k] for k in keep if k in f} for f in got]
    lines = [
        f"{' '.join(f['argv'])}: {f['status']} {f.get('tool_version', '')}".rstrip() for f in facts
    ]
    return O.ok("harness_probe", id=h.id, facts=facts, message=_clean("\n".join(lines)))
