"""`ddflow harness record <id>`: what is recorded, get a scratch project ready, or take a capture.

`services.harness_record` holds the taps, the scrub and the fixtures; this file is the three
verbs. Nothing here starts the agent or calls a model: a recording's session is the operator's,
so its cost is too. Over MCP this verb does not exist, it writes into a source tree.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import Config
from ..core import clock
from ..core import outcome as O
from ..core.redact import redact_network
from ..infra import fsio
from ..infra import git as G
from ..services import harness_record as HRec
from ..services import harnessreg as HR
from ..services.redact_report import redactor
from .setup.adopt import Adoption, files_tree, setup

KIND = "harness_record"


def _today() -> str:
    return clock.now_iso()[:10]


def _months(repo: Path) -> int:
    return Config.load(repo).hooks.fixture_max_age_months


def _table(rows: list[dict]) -> list[str]:
    return [
        f"{r['event']}: {r['status']} {r['version']} {r['date']}"
        + "".join(f"  [{f}]" for f in r["flags"])
        for r in rows
    ]


def _status(repo: Path, tree: Path, h: HR.Harness, dest: Path, installed: str) -> O.Outcome:
    fixtures = dest / h.id
    today, months = clock.parse_date(_today()), _months(repo)
    rows = HRec.rows(fixtures, today=today, months=months, installed=installed)
    lines = _table(rows) or [f"{h.id}: no fixtures under {dest}"]
    sheet = tree / HRec.CHECKLIST_ROOT / f"{h.id}.md"
    checklist = HRec.checklist_status(sheet, today=today, months=months) if sheet.is_file() else ""
    if checklist:
        lines.append(checklist)
    lines.append(f"next: ddflow harness record {h.id} --prepare <new scratch directory>")
    return O.ok(
        KIND,
        id=h.id,
        fixtures=rows,
        checklist=checklist,
        message=redact_network("\n".join(lines)),
    )


def _prepare(repo: Path, h: HR.Harness, target: str, agent: str) -> O.Outcome:
    why = HRec.untappable(h)
    if why:
        return O.nothing(KIND, why, id=h.id, status="unavailable", message=why)
    root = Path(target).resolve()
    if root.is_relative_to(Path(repo).resolve()):
        return O.refused(KIND, f"{root} is inside this project: use a directory outside it")
    try:
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            return O.refused(KIND, f"{root} is not empty: a recording starts from a new directory")
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return O.refused(KIND, f"cannot make {root}: {exc.strerror or exc}")
    if not G.init_repo(root):
        return O.failed(KIND, f"git init failed in {root}")
    adopted = setup(root, Adoption(agents=h.id), agent=agent)
    if adopted.exit != O.OK:
        return O.failed(KIND, f"adopt failed in the scratch project: {adopted.reason}")
    capture = root / HRec.CAPTURE_DIR
    capture.mkdir(mode=0o700)
    doc = HRec.snapshot(root, h)
    fsio.atomic_write(capture / "adopt.json", json.dumps(doc, indent=2) + "\n")
    tapped, why = HRec.tap_hooks(root, h, capture)
    mcp_note = HRec.tap_mcp(root, h, capture)
    stamp = {"agent": h.id, "root": str(root), "prepared": _today()}
    fsio.atomic_write(capture / HRec.MANIFEST, json.dumps(stamp, indent=2) + "\n")
    lines = [
        f"prepared {root} for {h.id}: adopted, nothing has run",
        f"hooks tapped: {', '.join(tapped) or 'none'}" + (f" ({why})" if why else ""),
        f"MCP tapped: {'yes' if not mcp_note else 'no (' + mcp_note + ')'}",
        "next: run ONE small session there yourself (it costs what a session costs): one prompt, "
        "one tool call, one ddflow_brief call, then exit",
        f"then: ddflow harness record {h.id} --from {root} --write",
    ]
    return O.ok(
        KIND,
        id=h.id,
        root=str(root),
        hooks_tapped=tapped,
        hooks_note=why,
        mcp_tapped=not mcp_note,
        mcp_note=mcp_note,
        message=redact_network("\n".join(lines)),
    )


def _from(
    repo: Path, h: HR.Harness, source: str, dest: Path, version: str, write: bool
) -> O.Outcome:
    src = Path(source).resolve()
    stamp = HRec.manifest(src)
    if stamp is None:
        return O.failed(KIND, f"{src} holds no capture: run --prepare first")
    if stamp.get("agent") != h.id:
        return O.refused(KIND, f"that capture is of {stamp.get('agent')!r}, not {h.id!r}")
    if not version:
        why = f"{h.id}'s version is unknown: pass --agent-version (a fixture always says it)"
        return O.nothing(KIND, why, id=h.id, status="unavailable", fixtures=[], message=why)
    cfg = Config.load(repo)
    scrub = HRec.Scrubber(
        root=str(stamp.get("root") or src),
        home=str(Path.home()),
        redactor=redactor("log", cfg),
        extra={str(Path(HRec.__file__).resolve().parents[2]): "<ddflow>"},
    )
    cap = cfg.hooks.fixture_max_reply_bytes
    when = str(stamp.get("prepared") or _today())  # the session's day, not this re-scrub's
    rec = HRec.build(h, src, scrub, version=version, today=when, reply_cap=cap)
    if not rec.files:
        why = f"{src} holds no session: nothing was captured"
        return O.nothing(KIND, why, id=h.id, status="empty", fixtures=[], message=why)
    if rec.leaks:
        kinds = ", ".join(rec.leaks)
        return O.failed(KIND, f"nothing written: a leak the scrub missed ({kinds})")
    fixtures = [
        {"event": rel.removesuffix(".json"), "bytes": len(json.dumps(doc))}
        for rel, doc in sorted(rec.files.items())
    ]
    wrote = HRec.write(dest / h.id, rec) if write else []
    lines = [f"{r['event']}: {r['bytes']} bytes" for r in fixtures]
    lines += [f"malformed, not written: {e}" for e in rec.malformed]
    lines.append(f"wrote {len(wrote)} under {dest / h.id}" if write else "not written: add --write")
    return O.ok(
        KIND,
        id=h.id,
        version=version,
        fixtures=fixtures,
        malformed=rec.malformed,
        written=wrote,
        message=redact_network("\n".join(lines)),
    )


def harness_record(
    repo: Path,
    id: str,
    *,
    prepare: str = "",
    source: str = "",
    write: bool = False,
    dest: str = "",
    version: str = "",
    agent: str = "",
    called_from: Path | None = None,
) -> O.Outcome:
    """One verb, three moods: status (no option), `prepare`, or `source` (`--from`)."""
    h = HR.get(id)
    if h is None:
        return O.failed(KIND, redact_network(f"no descriptor {id!r}: `ddflow harness list`"))
    if prepare and source:
        return O.failed(KIND, "--prepare and --from are two steps: a session runs between them")
    tree = files_tree(repo, called_from)
    target = Path(dest) if dest else tree / HRec.FIXTURE_ROOT
    if prepare:
        return _prepare(repo, h, prepare, agent)
    if source:
        return _from(repo, h, source, target, version, write)
    return _status(repo, tree, h, target, version)
