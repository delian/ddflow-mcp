"""The reproduction bundle `bug found` stores with a bug (DX-bug-repro-bundle).

A bug that cannot be reproduced usually lost the writer's context: which ddflow, which tree,
which environment variables (`DDFLOW_RUNNING_GATE` hid B29d63a65a2), which config layer won.
`capture` collects it when the bug is filed, without being asked, redacts it with the `log`
profile plus `[bugs] repro_redact_extra`, caps it at `[bugs] repro_max_bytes`, and stores it
as a content-addressed blob (`infra.blobstore`, as gate evidence is). The bug's event keeps
only `{"ref", "bytes", "mode", "truncated"}`; `read` gives the bundle back and `summary`
prints it. `[bugs] repro_capture = off` stores nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from ..core import blobref as B
from ..core import clock
from ..core.model import State
from ..core.redact import redact_leaves
from ..infra.blobstore import BlobStore
from . import logblobs
from . import repro_facts as F
from .redact_report import redactor

_MIN_LEAF = 64
_GUARD = 4000
MODES = ("off", "basic", "full")


def build(  # noqa: PLR0913 -- one keyword per thing the bundle is told
    repo: Path,
    cfg: Config,
    st: State,
    events: list[Any],
    *,
    bug: str,
    item: str = "",
    agent: str = "",
    test: str = "",
    repro_cmd: str = "",
    mode: str = "",
    now: float = 0.0,
) -> dict[str, Any] | None:
    """The bundle for ``bug`` at ``mode`` (default `[bugs] repro_capture`), redacted and
    capped, or None when the mode is `off`."""
    mode = mode or cfg.bugs.repro_capture
    if mode == "off":
        return None
    b = cfg.bugs
    out: dict[str, Any] = {
        "schema": 1,
        "bug": bug,
        "mode": mode,
        "collected_at": clock.now_iso(),
        "tree": F.tree(repo),
        "agent": F.identity(cfg, agent),
        "env": F.environment(cfg),
        "config_deltas": F.config_deltas(cfg),
        "commands": {
            "unavailable": "ddflow keeps no log of the commands a session ran (no session or "
            "hook command log exists); the agent's latest log events are listed under "
            "recent_events instead"
        },
    }
    if repro_cmd:
        out["repro_cmd"] = {"command": repro_cmd, "executed": False}
    if mode == "full":
        out["item"] = F.item_state(st, item, now or clock.now_utc().timestamp())
        out["machine"] = F.machine(repo)
        out["recent_events"] = F.recent_events(events, cfg.agent.id, b.repro_events)
        if test:
            out["test"] = F.test_run(repo, test, b.repro_tail_bytes, b.repro_test_timeout_s)
    elif test:
        out["test"] = {"id": test, "unavailable": "not run: [bugs] repro_capture is basic"}
    red = redactor("log", cfg)
    red.secret_patterns.extend(b.repro_redact_extra)
    clean = redact_leaves(out, red)
    return _cap(clean, b.repro_max_bytes)  # type: ignore[arg-type]


def _size(value: Any) -> int:
    return len(B.body(value))


def _shrink(node: Any) -> bool:
    """Make the largest part of ``node`` smaller by one step; False when nothing can shrink."""
    if isinstance(node, dict):
        keys = [k for k in node if k not in ("schema", "bug", "mode", "truncated")]
        if not keys:
            return False
        key = max(keys, key=lambda k: _size(node[k]))
        child = node[key]
        if isinstance(child, str) and len(child) > _MIN_LEAF:
            node[key] = child[: len(child) // 2] + "...[truncated]"
        elif isinstance(child, (dict, list)) and child:
            if not _shrink(child):
                del node[key]
        else:
            del node[key]
        return True
    if isinstance(node, list) and node:
        last = max(range(len(node)), key=lambda i: _size(node[i]))
        child = node[last]
        if isinstance(child, str) and len(child) > _MIN_LEAF:
            node[last] = child[: len(child) // 2] + "...[truncated]"
        elif isinstance(child, (dict, list)) and child and _shrink(child):
            pass
        else:
            del node[last]
        return True
    return False


def _cap(bundle: dict[str, Any], limit: int) -> dict[str, Any]:
    """``bundle`` shrunk, biggest part first, until it fits ``limit`` bytes; says so."""
    if _size(bundle) <= limit:
        return bundle
    bundle["truncated"] = True
    for _ in range(_GUARD):
        if _size(bundle) <= limit or not _shrink(bundle):
            break
    if _size(bundle) > limit:  # even the skeleton does not fit: keep what names it
        bundle = {k: bundle[k] for k in ("schema", "bug", "mode") if k in bundle}
        bundle["truncated"] = True
    return bundle


def store(repo: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    """Put ``bundle`` in the blob store; the link the bug's event carries."""
    raw = B.body(bundle)
    ref = B.address(raw)
    BlobStore(repo).put(ref, raw)
    return {
        "ref": ref,
        "bytes": len(raw),
        "mode": bundle.get("mode", ""),
        "truncated": bool(bundle.get("truncated")),
    }


def read(repo: Path, link: dict[str, Any] | None) -> Any:
    """The bundle a bug's link names, or an `Unavailable` saying why not; None for no link."""
    if not link or not link.get("ref"):
        return None
    return logblobs.fetch(BlobStore(repo), str(link["ref"]))


def summary(bundle: Any, link: dict[str, Any] | None = None) -> str:
    """The bundle in a few lines, for `show <bug>` and the fix task's claim."""
    if not isinstance(bundle, dict):
        return f"  repro bundle {bundle}" if bundle else ""
    t, a = bundle.get("tree", {}), bundle.get("agent", {})
    lines = [
        f"  repro bundle ({bundle.get('mode')}, {(link or {}).get('bytes', '?')} bytes"
        + (", TRUNCATED" if bundle.get("truncated") else "")
        + f") collected {bundle.get('collected_at', '?')}",
        f"    ddflow {t.get('ddflow_version', '?')} at {str(t.get('tree_sha', ''))[:12]} "
        f"on {t.get('branch', '?')} (main {str(t.get('main_sha', ''))[:12]}); cwd {t.get('cwd', '?')}",
        f"    agent {a.get('id', '?')} (from {a.get('derived_from', '?')})",
    ]
    if bundle.get("env"):
        lines.append("    env " + ", ".join(f"{k}={v}" for k, v in bundle["env"].items()))
    if bundle.get("config_deltas"):
        lines.append("    config " + ", ".join(bundle["config_deltas"]))
    m = bundle.get("machine")
    if m:
        lines.append(f"    load {m.get('load_avg')}, disk free {m.get('disk_free_mb')} MB")
    it = bundle.get("item")
    if it and "state" in it:
        lines.append(f"    item {it['id']} {it['state']}, gates: {', '.join(it['gates']) or '-'}")
    tst = bundle.get("test")
    if tst:
        lines.append(
            f"    test {tst.get('id')}: exit {tst.get('exit_code', tst.get('unavailable'))}"
        )
    if bundle.get("repro_cmd"):
        lines.append(f"    re-run: {bundle['repro_cmd']['command']}")
    lines.append(f"    ddflow --json show {bundle.get('bug', '<bug>')} has it whole")
    return "\n".join(lines)


def lead_with(out: Any, repo: Path, st: State, item: str) -> Any:
    """``out`` (a claim's answer), with the repro bundle of each bug ``item`` is the fix task
    of put FIRST in its guidance: the reader of a fix task starts from how the bug was seen."""
    notes = []
    for bug in st.bugs.values():
        if bug.fix_task == item and bug.repro:
            text = shown(repo, bug.repro).get("repro_text", "")
            if text:
                notes.append(f"## Repro bundle of bug {bug.id}\n{text}")
    if notes and out.data is not None:
        out.data["guidance"] = "\n\n".join([*notes, out.data.get("guidance", "")]).strip()
    return out


def shown(repo: Path | None, link: dict[str, Any] | None) -> dict[str, Any]:
    """What `show <bug>` adds to the bug's record: the bundle whole and its summary text;
    nothing for a bug that has none."""
    if not link or repo is None:
        return {}
    if link.get("unavailable"):
        return {"repro_text": f"  repro bundle not stored: {link['unavailable']}"}
    got = read(repo, link)
    return {"repro_bundle": got, "repro_text": summary(got, link)}


def mode_problem(capture: str) -> str:
    """Why ``--repro-capture`` ``capture`` is not a mode, or ""."""
    return "" if not capture or capture in MODES else f"repro_capture must be one of {MODES}"


def attach(repo: Path, cfg: Config, st: State, log: Any, bid: str, **given: Any) -> dict[str, Any]:
    """The `bug.found` field linking the bundle: ``{"repro": link}``, or ``{}`` when capture
    is off. Filing a bug must never fail for want of evidence: a bundle that cannot be built
    or stored is linked as ``{"unavailable": why}``, which `show` prints. ``given``: item,
    agent, test, repro_cmd, capture."""
    try:
        bundle = build(
            repo,
            cfg,
            st,
            log.read_all(),
            bug=bid,
            item=given.get("item", ""),
            agent=given.get("agent", ""),
            test=given.get("test", ""),
            repro_cmd=given.get("repro_cmd", ""),
            mode=given.get("capture", ""),
        )
        return {"repro": store(repo, bundle)} if bundle is not None else {}
    except Exception as exc:  # evidence is a courtesy; the bug is the point
        return {"repro": {"unavailable": f"{type(exc).__name__}: {exc}"[:300]}}
