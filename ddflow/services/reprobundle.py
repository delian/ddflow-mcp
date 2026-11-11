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

import math
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
from . import reprocheck as RC
from . import cmdlog
from .redact_report import redactor

_MIN_LEAF = 64
_GUARD = 4000
MODES = ("off", "basic", "full")


def _commands(repo: Path, bugs, mode: str) -> dict[str, Any]:
    """The last N commands this session ran (DX-command-log): the git-ignored local ring log
    when it has records (each already shape- and output-redacted at write time), else the note
    that none are recorded yet."""
    records = cmdlog.recent(repo, bugs.repro_events)
    if records:
        return {"last": records}
    return {
        "unavailable": "the command log has no records yet"
        + ("; see recent_events" if mode == "full" else "")
    }


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
    check: dict[str, Any] | None = None,
    machine: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The bundle for ``bug`` at ``mode`` (default `[bugs] repro_capture`), redacted and
    capped, or None when the mode is `off`."""
    mode = mode or cfg.bugs.repro_capture
    if mode not in MODES:
        raise ValueError(f"repro_capture {mode!r} is not one of {MODES}")
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
        "commands": _commands(repo, b, mode),
    }
    if repro_cmd:
        # The filed command wins any key the check dict might carry: what the operator
        # told ddflow to run is the record of what was filed (never silently replaced).
        # `executed` defaults in either branch: a check without the key means the run
        # happened, no check means it was recorded as given.
        out["repro_cmd"] = _canonical_cmd(repro_cmd, check)
    if mode == "full":
        out["item"] = F.item_state(st, item, now or clock.now_utc().timestamp())
        out["machine"] = machine if machine is not None else F.machine(repo)
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


def _tree_sha_safely(repo: Path) -> str:
    """The tree sha the repro would run at, or "": a tree that cannot be read degrades
    the evidence, never the run (check answers 'unavailable' to an empty sha)."""
    try:
        return (F.tree(repo) or {}).get("tree_sha") or ""
    except Exception:
        return ""


def _canonical_cmd(repro_cmd: str, check: dict[str, Any] | None) -> dict[str, Any]:
    """The recorded ``repro_cmd`` section: the filed command (last, wins any key), the
    evidence keys a check carried, and the four measurement keys CANONICAL: executed True
    only for a record with a real yes/no verdict and a whole runs count behind it, and a
    non-real run records no verdict and no failure count -- a no-run check cannot ride
    out carrying a 'yes'."""
    check_dict = dict(check) if check is not None else {}
    r = _countable(check_dict["runs"]) if "runs" in check_dict else 0
    real = check_dict.get("reproduced") in ("yes", "no") and r is not None and r > 0
    canonical = {
        "executed": real,
        "reproduced": check_dict.get("reproduced") if real else None,
        "runs": r,
        "failures": _countable(check_dict["failures"])
        if real and "failures" in check_dict
        else (0 if real and check_dict.get("reproduced") == "no" else None),
    }
    extra = {
        k: v
        for k, v in check_dict.items()
        if k not in ("executed", "reproduced", "runs", "failures")
    }
    return {
        **extra,
        **canonical,
        "command": repro_cmd,
    }


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


def lead_with(out: Any, repo: Path, events: Any, item: str) -> Any:
    """``out`` (a claim's answer), with the repro bundle of each bug ``item`` is the fix task
    of put FIRST in its guidance: the reader of a fix task starts from how the bug was seen.
    ``events`` is the log (the newest `bug.found` that names the link wins)."""
    links: dict[str, dict[str, Any]] = {}
    for e in events:
        if (
            e.kind == "bug.found"
            and e.data.get("repro")
            and item
            and e.data.get("fix_task") == item
        ):
            links[e.subject] = e.data["repro"]
    notes = []
    for bug, link in links.items():
        text = shown(repo, link).get("repro_text", "")
        if text:
            notes.append(f"## Repro bundle of bug {bug}\n{text}")
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


def _countable(value: Any) -> int | None:
    """``value`` as a whole, finite count, or None when it cannot count anything.
    Strings and integers stay exact (int() first on those types, where an exact answer
    exists); anything else goes through float and must be a whole non-negative number
    there -- a fractional count truncates to NOTHING, never to an integer."""
    if isinstance(value, str):
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return n if n >= 0 else None
    if isinstance(value, int):
        return value if value >= 0 else None
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(n) or n < 0 or not n.is_integer():
        return None
    return int(n)


def sighting(run: Any) -> dict[str, Any]:
    """The part of a run's record the bug's EVENT keeps beside the bundle link
    (DX-bug-repro-verify): the verdict the fold folds, nothing the blob already has. A run
    that reached no exit code (nothing to say) gives {}: a record with no RUNS behind it,
    a verdict that is not yes/no, a 'yes' with no failing run behind it, or counts that
    cannot count (fractional, negative, overflowing, failures beyond runs) is not a
    sighting, wherever it came from."""
    if not isinstance(run, dict) or "reproduced" not in run:
        return {}
    if run.get("reproduced") not in ("yes", "no"):
        return {}
    runs = _countable(run.get("runs", 0))
    failures = _countable(run.get("failures", 0))
    if runs is None or failures is None or runs <= 0 or failures > runs:
        return {}
    if run.get("reproduced") == "yes" and failures <= 0:
        return {}
    if run.get("reproduced") == "no" and failures > 0:
        return {}  # 'no' says no run failed: a failing count contradicts the verdict
    # Canonical: the PARSED counts go out, never the raw non-count the caller put in.
    return {"reproduced": run.get("reproduced"), "runs": runs, "failures": failures}


def attach(repo: Path, cfg: Config, st: State, log: Any, bid: str, **given: Any) -> dict[str, Any]:
    """The `bug.found` fields linking the bundle and the sighting: ``{"repro": link,
    "sighted": verdict}`` -- the sighting only when a run reached an exit code -- or
    ``{}`` + the sighting when capture is off (the run still happened; dropping its
    verdict would run the command and lose what it said). Filing a bug must never fail
    for want of evidence: a bundle that cannot be built or stored is linked as
    ``{"unavailable": why}``, which `show` prints, and the verdict still rides along.
    ``given``: item, agent, test, repro_cmd, capture."""
    events = None
    machine = None
    try:
        # Evidence reads degrade per-step, BEFORE the run: a log or machine probe that
        # cannot be read strips its own section, never the run -- the contract is that
        # the repro command STILL RUNS and its sighting is still recorded.
        run = given.get("check")
        if run is None and given.get("repro_cmd", ""):
            # Evidence reads degrade per-step, BEFORE the run, and a FAILED read degrades
            # to its terminal value AT ONCE -- [] / an unavailable marker -- so nothing is
            # retried after the run and post-run events can never masquerade as the
            # pre-run snapshot.
            if events is None:
                try:
                    events = log.read_all() if hasattr(log, "read_all") else log
                except Exception:
                    events = []
            if machine is None:
                try:
                    machine = F.machine(repo)
                except Exception:
                    machine = {"unavailable": "machine facts could not be collected"}
            run = RC.check(
                repo,
                _tree_sha_safely(repo),
                given.get("repro_cmd", ""),
                int(cfg.bugs.repro_runs),
                float(cfg.bugs.repro_test_timeout_s),
                int(cfg.bugs.repro_tail_bytes),
            )
        sig = sighting(run)
        try:
            # A caller-supplied check ran before attach, so no run of ours happened:
            # reading here is still pre-run; failed reads degrade terminally and never
            # surface over the verdict.
            if events is None:
                try:
                    events = log.read_all() if hasattr(log, "read_all") else log
                except Exception:
                    events = []
            if machine is None:
                try:
                    machine = F.machine(repo)
                except Exception:
                    machine = {"unavailable": "machine facts could not be collected"}
            bundle = build(
                repo,
                cfg,
                st,
                events,
                bug=bid,
                item=given.get("item", ""),
                agent=given.get("agent", ""),
                test=given.get("test", ""),
                repro_cmd=given.get("repro_cmd", ""),
                check=run,
                machine=machine,
                mode=given.get("capture", ""),
            )
            linked = {"repro": store(repo, bundle)} if bundle is not None else {}
        except Exception as exc:  # evidence is a courtesy; the bug is the point
            linked = {"repro": {"unavailable": f"{type(exc).__name__}: {exc}"[:300]}}
        return {**linked, **({"sighted": sig} if sig else {})}
    except Exception as exc:
        return {"repro": {"unavailable": f"{type(exc).__name__}: {exc}"[:300]}}
