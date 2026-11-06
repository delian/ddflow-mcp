"""From a live session to committed fixtures: taps, the scrub, the fixtures, and their age.

`ddflow harness record` (`api.harness_record`) drives three steps, none of which calls a model:

* **prepare** gives a scratch project a capture hook for every event the descriptor maps and
  stands `harness_tap` in front of the MCP server. Adopted hooks alone would not do: adopt
  installs a few events, and a recording owes all of them.
* the operator runs a small session there, headless or in the IDE: the step that costs money
  is theirs. B-ac-smoke owns the one headless runner.
* **from** turns the capture into `tests/fixtures/harness/<id>/<event>.json` plus
  `<id>/session/{mcp,adopt}.json`, which the conformance suite replays.

The fixtures are public files, so everything in them goes through `Scrubber`: this machine's
paths, the session's ids, then the `log` redaction profile. `leaks` checks the result and one
hit refuses the whole write: a scrub that missed a form must not be trusted for the rest.
Payload text is data and is only ever stored, never followed or executed.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..core import clock
from ..core.redact import Redactor
from ..infra import fsio
from . import harness_tap as Tap
from . import harnessreg as HR
from . import hookio
from . import hookwriters as HW
from .engines import mcp_shape
from .engines.safety import EngineError
from .hookwriters import flatlist

CAPTURE_DIR = ".ddflow-record"
MANIFEST = "manifest.json"
TAP_MODULE = "ddflow.services.harness_tap"
#: Where the conformance suite reads fixtures from, under the project root.
FIXTURE_ROOT = Path("tests") / "fixtures" / "harness"
SESSION_DIR = "session"
#: A tier-2 agent's walked checklist, under the project root.
CHECKLIST_ROOT = Path("docs") / "ddflow" / "agents" / "checklist"
#: What a real project path and home become, the forms the hand-made fixtures use.
SCRUBBED_ROOT = "/w"
SCRUBBED_HOME = "~"
SCRUBBED_ENCODED_HOME = "-home"
#: Home while the redactor runs: it takes every `~/...` for a path of this machine.
_HOME_MARK = "ddflowrecordhomemark"
_ENCODED_HOME_MARK = "ddflowrecordencodedhomemark"
#: Characters kept of one string: a tool's output is not what a fixture is for.
MAX_STRING = 4000
#: The key a reply over `[hooks].fixture_max_reply_bytes` is replaced by, so a cut reads as one.
TRUNCATED = "_truncated"
#: A session id shorter than this identifies nothing, and replacing it would mangle the text.
MIN_SESSION_ID = 8
#: Request/response pairs kept, one per method and tool.
MAX_PAIRS = 20
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_MAJOR = re.compile(r"\d+")
_WALKED = re.compile(r"^Date walked: (\S+)", re.M)
#: The facts `expect` pins beside the session id, when the normalizer finds them.
_GOLDEN = ("prompt", "source", "model", "cwd", "tool")


def encoded(path: str) -> str:
    """The form an agent uses for a directory name: every non-alphanumeric a dash."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


class Scrubber:
    """One recording's replacements, so an id reads the same in every file of it."""

    def __init__(self, *, root: str, home: str, redactor: Redactor, extra: dict | None = None):
        self._root = root
        self._home = home
        self._redactor = redactor
        pairs = {
            root: SCRUBBED_ROOT,
            encoded(root): encoded(SCRUBBED_ROOT),
            home: _HOME_MARK,
            encoded(home): _ENCODED_HOME_MARK,
            **(extra or {}),
        }
        real = {k: v for k, v in pairs.items() if len(k) > 1}  # a home of "/" names nothing
        self._literals = sorted(real.items(), key=lambda kv: -len(kv[0]))
        self._sessions: dict[str, str] = {}
        self._uuids: dict[str, int] = {}

    def session(self, raw: str) -> None:
        """Say a session id, so it reads `s-1` wherever it turns up."""
        if len(raw) >= MIN_SESSION_ID:
            self._sessions.setdefault(raw, f"s-{len(self._sessions) + 1}")

    def _uuid(self, m: re.Match[str]) -> str:
        n = self._uuids.setdefault(m.group(0).lower(), len(self._uuids) + 1)
        return f"00000000-0000-0000-0000-{n:012d}"

    def text(self, s: str) -> str:
        # Paths first, longest first: an id inside a path must not break the path's match.
        for raw, new in self._literals:
            s = s.replace(raw, new)
        for raw in sorted(self._sessions, key=len, reverse=True):
            s = s.replace(raw, self._sessions[raw])
        s = self._redactor.text(_UUID.sub(self._uuid, s)).text
        s = s.replace(_ENCODED_HOME_MARK, SCRUBBED_ENCODED_HOME).replace(_HOME_MARK, SCRUBBED_HOME)
        over = len(s) - MAX_STRING
        return f"{s[:MAX_STRING]}[cut {over} chars]" if over > 0 else s

    def value(self, v: Any) -> Any:
        """``v`` with every string scrubbed, keys included."""
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, dict):
            return {self.text(str(k)): self.value(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self.value(x) for x in v]
        return v

    def leaks(self, doc: Any) -> list[str]:
        """The KINDS of this machine's text still in ``doc``; never the text itself."""
        blob = json.dumps(doc, ensure_ascii=False)
        found = []
        if self._root in blob or encoded(self._root) in blob:
            found.append("project path")
        if len(self._home) > 1 and (self._home in blob or encoded(self._home) in blob):
            found.append("home")
        if any(raw in blob for raw in self._sessions):
            found.append("session id")
        host = self._redactor.hostname
        if host and re.search(rf"\b{re.escape(host)}\b", blob):
            found.append("hostname")
        return found


# --- prepare ---------------------------------------------------------------------------


def tap_argv(capture: Path, *args: str) -> list[str]:
    """The tap's command line: this interpreter, with this ddflow on its path."""
    package = str(Path(__file__).resolve().parents[2])
    return ["env", f"PYTHONPATH={package}", sys.executable, "-m", TAP_MODULE, *args]


def untappable(h: HR.Harness) -> str:
    """Why a capture of ``h`` would hold nothing at all; "" when something can be tapped."""
    why = HW.unsupported(h)
    if not why:
        return ""
    try:
        mcp_shape.container_path(h.mcp.shape)
    except EngineError:
        return f"{why}, and its MCP config is not a JSON file ddflow can tap"
    return ""


def tap_hooks(root: Path, h: HR.Harness, capture: Path) -> tuple[list[str], str]:
    """One capture hook per mapped event, beside what adopt wrote: (events tapped, why not)."""
    why = HW.unsupported(h)
    if why:
        return [], why
    hk = h.hooks
    assert hk is not None
    path = root / hk.file
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    hooks = data.setdefault("hooks", {})
    for event, native in sorted(hk.events.items()):
        line = shlex.join(tap_argv(capture, "hook", str(capture), event))
        if hk.style in HW.JSON_GROUPS:
            entry: dict[str, Any] = {"hooks": [{"type": "command", "command": line}]}
        else:
            key, const = flatlist.LAYOUTS[hk.style]
            data.setdefault("version", 1)
            entry = {**const, key: line}
        hooks.setdefault(native, []).append(entry)
    fsio.atomic_write(path, json.dumps(data, indent=2) + "\n")
    return sorted(hk.events), ""


def tap_mcp(root: Path, h: HR.Harness, capture: Path) -> str:
    """Stand the tap in front of ddflow's MCP entry; "" when done, else why it could not."""
    path = root / h.mcp.path
    try:
        keys = mcp_shape.container_path(h.mcp.shape)
    except EngineError:
        return f"{h.id}: no JSON container for the {h.mcp.shape!r} MCP shape"
    if not path.is_file():
        return f"{h.id}: adopt wrote no {h.mcp.path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    node = data
    for k in keys:
        node = node.get(k, {})
    entry = node.get("ddflow")
    if not isinstance(entry, dict):
        return f"{h.id}: no ddflow entry in {h.mcp.path}"
    head = tap_argv(capture, "mcp", str(capture), "--")
    command = entry.get("command")
    if isinstance(command, list):  # opencode's shape: one list
        entry["command"] = [*head, *command]
    elif isinstance(command, str):
        entry["command"] = head[0]
        entry["args"] = [*head[1:], command, *entry.get("args", [])]
    else:
        return f"{h.id}: the ddflow entry in {h.mcp.path} has no command"
    fsio.atomic_write(path, json.dumps(data, indent=2) + "\n")
    return ""


def snapshot(root: Path, h: HR.Harness) -> dict[str, Any]:
    """What adopt wrote: every path, and the two config files whole (the agent reads those)."""
    skip = {".git", ".ddflow", CAPTURE_DIR}
    wrote = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not skip & set(p.relative_to(root).parts)
    )
    config = {}
    for rel in filter(None, (h.mcp.path, h.hooks.file if h.hooks else "")):
        p = root / rel
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            config[rel] = json.loads(text) if rel.endswith(".json") else text
    return {"wrote": wrote, "config": config}


# --- the capture -----------------------------------------------------------------------


@dataclass
class Recording:
    """What a capture would write, by path under the agent's fixture directory."""

    files: dict[str, dict] = field(default_factory=dict)
    malformed: list[str] = field(default_factory=list)
    leaks: list[str] = field(default_factory=list)


def manifest(source: Path) -> dict | None:
    path = source / CAPTURE_DIR / MANIFEST
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _payloads(capture: Path) -> dict[str, list[bytes]]:
    """Every stored payload by event, oldest first."""
    out: dict[str, list[bytes]] = {}
    for p in sorted((capture / Tap.HOOKS_DIR).glob("*.json")):
        out.setdefault(p.stem.rsplit("-", 1)[1], []).append(p.read_bytes())
    return out


def _rows(path: Path) -> list[dict]:
    """The log's JSON-RPC objects. Anything else on the wire, or a cut last line, is skipped."""
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(raw)
            frame = json.loads(row["line"])
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(frame, dict):
            out.append({"d": row.get("d"), "frame": frame})
    return out


def _pairs(capture: Path) -> list[dict]:
    """Each request with its reply, one per method and tool, in the order they were answered."""
    path = capture / Tap.MCP_LOG
    if not path.is_file():
        return []
    asked: dict[Any, dict] = {}
    pairs: list[dict] = []
    seen: set[tuple] = set()
    for row in _rows(path):
        frame = row["frame"]
        wire_id = frame.get("id")
        if not isinstance(wire_id, (str, int)):
            continue  # a notification has no reply to pair
        if row["d"] == "in" and isinstance(frame.get("method"), str):
            asked[wire_id] = frame
        elif row["d"] == "out" and wire_id in asked:
            req = asked.pop(wire_id)
            params = req.get("params")
            key = (req["method"], str(params.get("name")) if isinstance(params, dict) else "")
            if key not in seen and len(pairs) < MAX_PAIRS:
                seen.add(key)
                pairs.append({"method": req["method"], "request": req, "response": frame})
    return pairs


def _capped(pair: dict, cap: int) -> dict:
    """``pair`` with a reply over ``cap`` bytes replaced by a record of the cut, by size alone.
    Runs on a scrubbed pair, so what is kept was redacted whole first."""
    reply = pair["response"]
    size = len(json.dumps(reply, ensure_ascii=False).encode())
    if size <= cap:
        return pair
    cut = {"id": reply.get("id"), TRUNCATED: {"bytes": size, "cap": cap}}
    return {**pair, "response": cut}


def _meta(version: str, today: str, source: str) -> dict[str, Any]:
    return {
        "recorded": True,
        "synthetic": False,
        "source": source,
        "version": version,
        "date": today,
    }


def _expect(h: HR.Harness, event: str, stdin: dict, normalizer: str) -> dict[str, Any]:
    got = hookio.normalize(normalizer, event, h.id, json.dumps(stdin))
    out: dict[str, Any] = {"session_id": got.session_id}
    out.update({k: getattr(got, k) for k in _GOLDEN if getattr(got, k)})
    return out


def build(
    h: HR.Harness,
    source: Path,
    scrubber: Scrubber,
    *,
    version: str,
    today: str,
    reply_cap: int,
) -> Recording:
    """The fixtures of one capture, scrubbed: the first payload of each event."""
    capture = source / CAPTURE_DIR
    plan = hookio.plan_for(h.id)
    rec = Recording()
    firsts: dict[str, dict] = {}
    for event, raws in _payloads(capture).items():
        try:
            payload = json.loads(raws[0])
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or plan is None:
            rec.malformed.append(event)
            continue
        firsts[event] = payload
        scrubber.session(
            hookio.normalize(plan.normalizer, event, h.id, json.dumps(payload)).session_id
        )
    hook_source = "live hook stdin, `ddflow harness record`, scrubbed"
    for event, payload in firsts.items():
        stdin = scrubber.value(payload)
        rec.files[f"{event}.json"] = {
            "_meta": _meta(version, today, hook_source),
            "stdin": stdin,
            "expect": _expect(h, event, stdin, plan.normalizer),
        }
    pairs = [_capped(scrubber.value(p), reply_cap) for p in _pairs(capture)]
    if pairs:
        rec.files[f"{SESSION_DIR}/mcp.json"] = {
            "_meta": _meta(
                version,
                today,
                "live MCP traffic through the tap, scrubbed",
            ),
            "pairs": pairs,
        }
    adopted = capture / "adopt.json"
    if adopted.is_file() and rec.files:
        rec.files[f"{SESSION_DIR}/adopt.json"] = {
            "_meta": _meta(version, today, "what `ddflow adopt` wrote, scrubbed"),
            **scrubber.value(json.loads(adopted.read_text(encoding="utf-8"))),
        }
    rec.leaks = sorted({kind for doc in rec.files.values() for kind in scrubber.leaks(doc)})
    return rec


def write(dest: Path, rec: Recording) -> list[str]:
    for rel, doc in rec.files.items():
        fsio.atomic_write(dest / rel, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return sorted(rec.files)


# --- age -------------------------------------------------------------------------------


def _date(text: object) -> date | None:
    try:
        return clock.parse_date(str(text))
    except ValueError:
        return None


def _older(then: date, today: date, months: int) -> bool:
    """Is ``then`` more than ``months`` calendar months before ``today``?"""
    whole = (today.year - then.year) * 12 + today.month - then.month
    return whole > months or (whole == months and today.day > then.day)


def _major(version: object) -> str:
    m = _MAJOR.search(str(version))
    return m.group(0) if m else ""


def fixture_flags(meta: dict, *, today: date, months: int, installed: str = "") -> list[str]:
    """Why a recording should be taken again. A synthetic fixture was never a recording:
    its own status says that, so it is not aged here."""
    if meta.get("recorded") is not True:
        return []
    flags = []
    when = _date(meta.get("date"))
    if when is None:
        flags.append("recording has no date")
    elif _older(when, today, months):
        flags.append(f"older than {months} months ({when.isoformat()})")
    have, want = _major(meta.get("version")), _major(installed)
    if have and want and have != want:
        flags.append(f"recorded on major {have}, installed is major {want}")
    return flags


def checklist_status(path: Path, *, today: date, months: int) -> str:
    """What a tier-2 agent's report line says about its checklist (B-ac-report prints it)."""
    m = _WALKED.search(path.read_text(encoding="utf-8"))
    when = _date(m.group(1)) if m else None
    if when is None:
        return "checklist never walked"
    line = f"fixture only, checklist dated {when.isoformat()}"
    if _older(when, today, months):
        return f"{line}, older than {months} months"
    return line


_STATUS = {True: "recorded", None: "unreadable"}


def rows(fixtures: Path, *, today: date, months: int, installed: str = "") -> list[dict]:
    """Every committed fixture of one agent, for `harness record <id>` and the report."""
    out = []
    for p in sorted(fixtures.rglob("*.json")):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))["_meta"]
        except (ValueError, KeyError, TypeError):
            meta = None
        if not isinstance(meta, dict):
            meta = {"recorded": None}
        out.append(
            {
                "event": p.relative_to(fixtures).with_suffix("").as_posix(),
                "status": _STATUS.get(meta.get("recorded"), "synthetic"),
                "version": meta.get("version", "-"),
                "date": meta.get("date", "-"),
                "flags": fixture_flags(meta, today=today, months=months, installed=installed),
            }
        )
    return out
