"""Event triggers: definitions, validation and the evaluator (D-sched-triggers-separate,
D-trigger-actions-create-items).

A trigger watches the event log. When N matching events arrive within a window it FILES A
QUEUE ITEM from a scheduled job's template -- it never runs an agent -- and the normal
lease, gates and cross-family review then apply. Every evaluation that finds a condition
met is recorded: `trigger.fired` with the items it filed, or `trigger.suppressed` with why
not (disabled, debounce, cooldown, an open remediation for the key, the trigger's own cap,
the global hourly cap, the hop limit, the circuit breaker). `trigger.evaluated` records
each evaluator run.

Definitions live in `.ddflow/triggers/<id>.toml`, one per file, reviewed in git like any
config: editing an advisory rule can never arm automation. A trigger starts DISABLED:
`enabled = true` is the operator's explicit act.

Storm limits, three levels: one open remediation per dedupe key; a per-trigger cap
(`max_open`) and a global hourly cap; and a hop limit, so a remediation's own failure
cannot re-trigger it without bound. A trigger never counts events about the items it
filed itself, nor any `trigger.*` event. After `breaker` consecutive remediations that
failed (abandoned or removed) or yielded nothing (done without a merge), the trigger is
held until its definition changes.
"""

from __future__ import annotations

import fnmatch
import json
import re
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..core import clock
from ..core.digest import content_digest
from ..core.ids import free
from ..core.model import ABANDONED, DONE, TRIGGER_FIRES_KEPT, State
from . import schedule as SV

TRIGGERS_DIR = Path(".ddflow") / "triggers"
#: How many of a trigger's latest suppressions `show` lists (all are in the log).
SHOWN_SUPPRESSIONS = 20
FIELDS = (
    "title",
    "event",
    "match",
    "count",
    "window",
    "key",
    "debounce",
    "cooldown",
    "max_open",
    "hop_limit",
    "breaker",
    "action",
    "enabled",
    "tags",
)
_KEY_FIELD = re.compile(r"\{([^{}]*)\}")


@dataclass
class Trigger:
    id: str
    title: str = ""
    #: An event kind, or a glob over kinds (`gate.*`). Never a `trigger.*` kind.
    event: str = ""
    #: data field (or `subject` / `agent`) -> glob its value must match.
    match: dict[str, str] = field(default_factory=dict)
    count: int = 1  #: N matching events ...
    window: int = 0  #: ... within this many minutes (0: since the key last fired)
    #: The dedupe key, a template over `{subject}`, `{agent}`, `{kind}`, `{data.X}`: one
    #: open remediation per key. "" -- one per trigger.
    key: str = ""
    debounce: int = 0  #: minutes without a new matching event before it fires
    cooldown: int = 60  #: minutes after a fire before the same key may fire again
    max_open: int = 3  #: open remediations of this trigger, at most
    hop_limit: int = 1  #: how deep a chain of remediations may go
    breaker: int = 3  #: consecutive failed or zero-yield remediations that hold it
    #: {"job": "<scheduled job id>", "phase": "<optional parent>"}: what a fire files.
    action: dict[str, str] = field(default_factory=dict)
    enabled: bool = False
    tags: list[str] = field(default_factory=list)

    def digest(self) -> str:
        """The definition, fingerprinted: a breaker holds until this changes."""
        d = asdict(self)
        d.pop("enabled", None)
        return content_digest(json.dumps(d, sort_keys=True), length=12)


# -- one definition ------------------------------------------------------------------------


def _int(name: str, lo: int, hi: int | None = None):
    def check(v: Any, errors: list[str]) -> Any:
        if not isinstance(v, int) or isinstance(v, bool) or v < lo or (hi and v > hi):
            top = f" and at most {hi}" if hi else " or more"
            errors.append(f"{name} must be a whole number, {lo}{top}, got {v!r}")
            return None
        return v

    return check


def _str_map(name: str):
    def check(v: Any, errors: list[str]) -> Any:
        if not isinstance(v, dict) or not all(
            isinstance(k, str) and isinstance(x, str) for k, x in v.items()
        ):
            errors.append(f"{name} must be a table of strings, got {v!r}")
            return None
        return dict(v)

    return check


def _event(v: Any, errors: list[str]) -> Any:
    if not isinstance(v, str) or not v.strip():
        errors.append(f"event must be an event kind or a glob over kinds, got {v!r}")
        return None
    if fnmatch.fnmatchcase("trigger.fired", v) or v.startswith("trigger."):
        errors.append(f"event {v!r} matches trigger.* events: a trigger never reads its own output")
        return None
    return v.strip()


def _key(v: Any, errors: list[str]) -> Any:
    if not isinstance(v, str):
        errors.append(f"key must be a template string, got {v!r}")
        return None
    for name in _KEY_FIELD.findall(v):
        if name not in ("subject", "agent", "kind") and not (
            name.startswith("data.") and name != "data."
        ):
            errors.append(f"key field {{{name}}} is not subject, agent, kind or data.<field>")
    return v


def _action(v: Any, errors: list[str]) -> Any:
    out = _str_map("action")(v, errors)
    if out is None:
        return None
    if set(out) - {"job", "phase"} or not out.get("job"):
        errors.append(f'action must be {{job = "<scheduled job id>"}} (and phase), got {v!r}')
        return None
    return out


def _title(v: Any, errors: list[str]) -> Any:
    if not isinstance(v, str):
        errors.append(f"title must be a string, got {v!r}")
        return None
    return v.strip()


def _enabled(v: Any, errors: list[str]) -> Any:
    if not isinstance(v, bool):
        errors.append(f"enabled must be true or false, got {v!r}")
        return None
    return v


#: One checker per field: (value, errors) -> the typed value; it appends what is wrong.
_CHECKS = {
    "title": _title,
    "event": _event,
    "match": _str_map("match"),
    "count": _int("count", 1),
    "window": _int("window", 0),
    "key": _key,
    "debounce": _int("debounce", 0),
    "cooldown": _int("cooldown", 0),
    "max_open": _int("max_open", 1),
    "hop_limit": _int("hop_limit", 0),
    "breaker": _int("breaker", 1, TRIGGER_FIRES_KEPT),
    "action": _action,
    "enabled": _enabled,
    "tags": lambda v, e: SV._str_list("tags", v, e),
}


def build(tid: str, spec: dict[str, Any]) -> tuple[Trigger | None, list[str]]:
    """A trigger from `spec`, or None and every reason why not."""
    errors = [e for e in [SV.check_id(tid).replace("job id", "trigger id")] if e]
    unknown = sorted(set(spec) - set(FIELDS))
    if unknown:
        errors.append(f"unknown field(s) {', '.join(unknown)}: a trigger has {', '.join(FIELDS)}")
    for need in ("event", "action"):
        if need not in spec:
            errors.append(f"{need} is required")
    fields = {k: _CHECKS[k](spec[k], errors) for k in FIELDS if k in spec}
    if errors:
        return None, errors
    return Trigger(id=tid, **fields), []


def load(repo: Path, jobs: dict[str, Any]) -> tuple[dict[str, Trigger], list[str]]:
    """Every `.ddflow/triggers/*.toml`, id = file name. A broken file, or one whose action
    names no scheduled job, is reported and left out; it never stops the rest."""
    d = Path(repo) / TRIGGERS_DIR
    out: dict[str, Trigger] = {}
    errors: list[str] = []
    if not d.is_dir():
        return out, errors
    for f in sorted(d.glob("*.toml")):
        rel = (TRIGGERS_DIR / f.name).as_posix()
        try:
            spec = tomllib.loads(f.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"{rel}: cannot read it: {exc}")
            continue
        if spec.pop("id", f.stem) != f.stem:
            errors.append(f"{rel}: its id differs from the file name; a trigger file is <id>.toml")
            continue
        trig, bad = build(f.stem, spec)
        if trig is not None and trig.action["job"] not in jobs:
            bad = [f"action.job {trig.action['job']!r} is not a scheduled job"]
            trig = None
        if trig is None:
            errors.append(f"{rel}: " + "; ".join(bad))
            continue
        out[f.stem] = trig
    return out, errors


# -- the evaluator -------------------------------------------------------------------------


@dataclass
class Decision:
    """What one evaluation of one (trigger, key) decided: fire, or the reason it did not."""

    trigger: str
    key: str
    fire: bool
    reason: str = ""
    detail: str = ""
    events: list[str] = field(default_factory=list)
    hop: int = 0
    #: The items a fire filed (set when it is applied).
    items: list[str] = field(default_factory=list)


def ts(text: str) -> datetime | None:
    try:
        return clock.parse_ts(str(text), naive="utc")
    except clock.UNPARSEABLE:
        return None


def _field(ev, name: str) -> str:
    if name in ("subject", "agent", "kind"):
        return str(getattr(ev, name, "") or "")
    if name.startswith("data."):
        name = name[5:]
    v = (ev.data or {}).get(name, "")
    return v if isinstance(v, str) else json.dumps(v, sort_keys=True)


def render_key(template: str, ev) -> str:
    return _KEY_FIELD.sub(lambda m: _field(ev, m.group(1)), template)


def matches(trig: Trigger, ev) -> bool:
    """Does `ev` count for `trig`? Never a `trigger.*` event: a trigger does not read its
    own output (nor another trigger's bookkeeping)."""
    if ev.kind.startswith("trigger.") or not fnmatch.fnmatchcase(ev.kind, trig.event):
        return False
    return all(fnmatch.fnmatchcase(_field(ev, k), pat) for k, pat in trig.match.items())


def outcome(st: State, item: str) -> str:
    """A remediation item: open | success (done and merged) | zero (done, nothing merged)
    | failed (abandoned or removed)."""
    it = st.items.get(item)
    if it is None or it.removed or it.state == ABANDONED:
        return "failed"
    if it.state != DONE:
        return "open"
    return "success" if it.merged_sha else "zero"


def _held(st: State, trig: Trigger) -> int:
    """How many remediations in a row, newest first, of the CURRENT definition failed or
    yielded nothing. Open ones are skipped: they have not said yet."""
    digest = trig.digest()
    n = 0
    for fire in reversed(st.trigger_fires.get(trig.id, [])):
        if fire.get("digest") != digest:
            break
        results = [outcome(st, i) for i in fire.get("items", [])]
        if not results or all(r == "open" for r in results):
            continue
        if "success" in results:
            break
        n += 1
    return n


def _ledger(st: State, tid: str) -> tuple[dict[str, datetime], dict[str, list[str]]]:
    """(key -> when it last fired, key -> its remediation items still open). Open items
    come from `trigger_items`, every item a trigger filed: a reopened older item of a key
    counts as open whichever fire was its key's latest (roborev)."""
    last_at: dict[str, datetime] = {}
    for key, f in st.trigger_keys.get(tid, {}).items():
        t = ts(f.get("at", ""))
        if t is not None:
            last_at[key] = t
    open_by_key: dict[str, list[str]] = {}
    for item, m in st.trigger_items.items():
        if m.get("trigger") == tid and outcome(st, item) == "open":
            open_by_key.setdefault(str(m.get("key", "")), []).append(item)
    return last_at, open_by_key


def _groups(st: State, trig: Trigger, events: list, last_at: dict, now: datetime) -> dict:
    """key -> [(ts, event)] that count now: matching, not about this trigger's own items,
    after the key last fired, not in the future. `cluster` applies the window."""
    own = {i for i, m in st.trigger_items.items() if m.get("trigger") == trig.id}
    out: dict[str, list] = {}
    for ev in events:
        if ev.subject in own or not matches(trig, ev):
            continue
        t = ts(ev.ts)
        if t is None or t > now:
            continue
        key = render_key(trig.key, ev)
        since = last_at.get(key)
        if since is not None and t <= since:
            continue
        out.setdefault(key, []).append((t, ev))
    return out


def cluster(evs: list, count: int, window: int) -> list:
    """The latest run of `count` or more events lying within `window` minutes of each
    other (a sliding window over their times), or [] when there is none. No window: all.
    Neither anchored on `now` -- a debounce longer than the gap aged the older events out
    -- nor on the newest event -- one late straggler hid an earlier burst (rubber-duck)."""
    evs = sorted(evs, key=lambda x: x[0])
    if not window:
        return evs if len(evs) >= count else []
    span = timedelta(minutes=window)
    i = len(evs) - 1
    for j in range(len(evs) - 1, -1, -1):
        i = min(i, j)
        while i > 0 and evs[j][0] - evs[i - 1][0] <= span:
            i -= 1
        if j - i + 1 >= count:
            return evs[i : j + 1]
    return []


def _why_not(
    st: State, trig: Trigger, d: Decision, newest: datetime, since, open_by_key, now, hour
) -> tuple[str, str]:
    """The first storm limit that holds `d` back, as (reason, detail); ("", "") to fire."""
    n_open = sum(len(v) for v in open_by_key.values())
    held = _held(st, trig)
    checks = (
        (not trig.enabled, "disabled", "enable it with enabled = true in its file"),
        (
            bool(trig.debounce) and newest > now - timedelta(minutes=trig.debounce),
            "debounce",
            f"a matching event at {newest.isoformat()}",
        ),
        (
            since is not None and since > now - timedelta(minutes=trig.cooldown),
            "cooldown",
            f"fired at {since.isoformat() if since else ''}",
        ),
        (bool(open_by_key.get(d.key)), "open", ", ".join(open_by_key.get(d.key, []))),
        (n_open >= trig.max_open, "max_open", f"{n_open} open of max_open {trig.max_open}"),
        (d.hop > trig.hop_limit, "hop_limit", f"hop {d.hop} over hop_limit {trig.hop_limit}"),
        (
            held >= trig.breaker,
            "breaker",
            f"{held} remediations in a row failed or yielded nothing; change the definition "
            f"to re-arm it",
        ),
        (
            hour[0] >= hour[1],
            "global_cap",
            f"{hour[0]} fires in the last hour; [triggers].max_fires_per_hour = {hour[1]}",
        ),
    )
    return next(((r, why) for hit, r, why in checks if hit), ("", ""))


def evaluate(
    st: State,
    events: list,
    triggers: dict[str, Trigger],
    now: datetime,
    *,
    max_per_hour: int,
) -> list[Decision]:
    """Every (trigger, key) whose condition is met now, and what it decided. Pure: the
    caller writes the events and files the items. ``max_per_hour`` is
    `[triggers].max_fires_per_hour` (D-trigger-cap-knob), required so no caller can fall
    back to a cap the operator did not set: 0 suppresses every fire as
    `global_cap`. The hourly cap is counted from each trigger's fire tail, so it may not
    exceed what the tail keeps."""
    if not 0 <= max_per_hour <= TRIGGER_FIRES_KEPT:
        raise ValueError(f"max_per_hour must be 0..{TRIGGER_FIRES_KEPT}, got {max_per_hour}")
    out: list[Decision] = []
    hour_ago = now - timedelta(hours=1)
    fired = sum(
        1
        for fires in st.trigger_fires.values()
        for f in fires
        if (t := ts(f.get("at", ""))) is not None and t > hour_ago
    )
    for tid, trig in sorted(triggers.items()):
        last_at, open_by_key = _ledger(st, tid)
        for key, matched in sorted(_groups(st, trig, events, last_at, now).items()):
            evs = cluster(matched, trig.count, trig.window)
            if len(evs) < trig.count:
                continue
            # Over EVERY new event of the key, not only the cluster: a remediation's own
            # failure among them must count against the hop limit (rubber-duck).
            hop = 1 + max(st.trigger_items.get(ev.subject, {}).get("hop", 0) for _, ev in matched)
            d = Decision(tid, key, False, events=[ev.id for _, ev in evs], hop=hop)
            # the quiet period runs from the newest matching event, in the cluster or not
            newest = max(t for t, _ in matched)
            d.reason, d.detail = _why_not(
                st, trig, d, newest, last_at.get(key), open_by_key, now, (fired, max_per_hour)
            )
            if not d.reason:
                d.fire = True
                fired += 1
                open_by_key.setdefault(key, []).append("(this fire)")
            out.append(d)
    return out


def item_for(trig: Trigger, job, d: Decision, taken: set[str], st: State) -> dict[str, Any]:
    """The queue item one fire files, from its job's template: the job's title, scope and
    prompt, tagged with the trigger, the job, the mode, the key and the trigger's own
    `tags`, under `action.phase` if given."""
    # Every item this trigger ever filed, not the capped fire tail (roborev: O(N) scans).
    n = sum(1 for m in st.trigger_items.values() if m.get("trigger") == trig.id) + 1
    iid = free(f"T-{trig.id}-{n}", taken, numbered=lambda k: f"T-{trig.id}-{k}", start=n + 1)
    what = job.title or job.id
    body = "\n".join(
        [
            f"Filed by trigger {trig.id} ({trig.title or trig.event})"
            + (f" for key {d.key!r}" if d.key else "")
            + f": {len(d.events)} matching event(s), hop {d.hop}.",
            f"Job template: {job.id} (mode {job.mode}, prompt {job.prompt or job.id}).",
            "Events: " + ", ".join(d.events[-10:]),
        ]
    )
    data: dict[str, Any] = {
        "title": f"{what}: {trig.id}" + (f" [{d.key}]" if d.key else ""),
        "body": body,
        "globs": list(job.scope_globs),
        "tags": list(
            dict.fromkeys(
                [
                    f"trigger:{trig.id}",
                    f"schedule:{job.id}",
                    f"mode:{job.mode}",
                    *([f"key:{d.key}"] if d.key else []),
                    *trig.tags,
                ]
            )
        ),
    }
    if trig.action.get("phase"):
        data["parent"] = trig.action["phase"]
    return {"id": iid, "data": data}
