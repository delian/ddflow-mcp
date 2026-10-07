"""`ddflow upgrade --plan`: what upgrading this project to the running ddflow would change.

Decision D-upgrade-model (3), task B-upgrade.3-plan. The plan only READS: it asks the pieces
that already know (the data repairs, the upgrade manifest, config source tracking, the
driver/rules checks, the hook and launcher checks) and lays their answers side by side in
one list, grouped by CATEGORY:

- `repairs`: data damage an older ddflow left in the log that a repair would mend;
- `config`: knobs added, knobs whose default changed and knobs removed since the version this
  project last worked under (the manifest, `services.upgrade_manifest`), judged against THIS
  project's config -- a knob it never set takes the new default; one anyone set (a config
  layer, the environment, `ddflow config`) is the operator's and is never changed without
  their confirmation (decision D-upgrade-config-changes);
- `instructions`: driver docs, rules blocks and native rules whose bytes differ from the ones
  this ddflow ships, and whether the drift is a release's (`stale`) or an edit (`hand-edited`);
- `hooks`: git and harness hooks that point at a launcher that is gone, and the harness hooks
  an adopted agent should have and does not;
- `mcp`: an MCP entry that launches a ddflow that is gone;
- `features`: opt-in features a release announced, with the command that enables each.

Every item carries an `action`: `agent` (an agent may apply it), `operator` / `needs operator
confirmation` (only with the operator's say) or `note` (nothing to apply; it is told so the
operator knows). An empty plan means the project is up to date.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.events import is_older, version_key
from ..core.model import State
from ..infra.log import running_version
from . import adopt as AD
from . import claudehooks as CH
from . import launchers as LA
from . import repairs as RP
from . import upgrade_manifest as UM

CATEGORIES = ("repairs", "config", "instructions", "hooks", "mcp", "features")

AGENT = "agent"
OPERATOR = "needs operator confirmation"
NOTE = "note"

#: Said after a remedy that is the apply step, which this ddflow does not have yet.
NOT_YET = " (not available yet: this ddflow only plans)"

_SHOWN = 8  #: findings quoted per repair in the text


def project_version(st: State, has_history: bool, running: str) -> str:
    """The release this project was last brought up to: the target of the last applied
    upgrade, else the OLDEST version that ever wrote to its log, else the running one when
    nothing was ever written, else "" (written before versions were stamped: every change
    since the manifest's base is news to it)."""
    if st.upgrades:
        last = str(st.upgrades[-1].get("to", ""))
        if version_key(last):
            return last
    seen = sorted(st.ddflow_versions, key=lambda v: (version_key(v), v))
    if seen:
        return seen[0]
    return "" if has_history else running


def _wanted(c: UM.Change, baseline: str, running: str) -> bool:
    """A change this ddflow brings to a project last at ``baseline``: not one of a release
    newer than the running code, and the unreleased ones only for a project older than it."""
    if c.version == UM.UNRELEASED:
        return not baseline or is_older(baseline, running)
    return not is_older(running, c.version)


def _since(baseline: str, running: str, manifest: UM.Manifest) -> list[UM.Change]:
    return [c for c in UM.changes_since(baseline, manifest) if _wanted(c, baseline, running)]


def _net(changes: list[UM.Change]) -> dict[str, tuple[str, UM.Change, Any, Any]]:
    """Per knob, what the releases since ``baseline`` add up to: `(kind, last change, old,
    new)`, oldest first. Added then changed is an addition with the last default; a knob
    changed back to what it was is no change."""
    out: dict[str, tuple[str, UM.Change, Any, Any]] = {}
    for c in changes:
        if not c.kind.startswith("knob_"):
            continue
        prev = out.get(c.key)
        if c.kind == "knob_removed":
            if prev is not None and prev[0] == "knob_added":
                del out[c.key]  # added and removed inside the window: it never reached the project
            else:
                out[c.key] = ("knob_removed", c, c.old, None)
        elif prev is not None and prev[0] == "knob_removed":
            # It existed before the baseline (its removal is the first thing that
            # happened to it), so coming back is a changed default, not a new knob.
            out[c.key] = ("knob_changed", c, prev[2], c.new)
        elif prev is None:
            out[c.key] = (c.kind, c, c.old, c.new)
        elif prev[0] == "knob_added":
            out[c.key] = ("knob_added", c, None, c.new)
        else:
            out[c.key] = ("knob_changed", c, prev[2], c.new)
    return {k: v for k, v in out.items() if not (v[0] == "knob_changed" and v[2] == v[3])}


def _short(value: Any, limit: int = 60) -> str:
    text = json.dumps(value, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _current(cfg: Config, key: str) -> Any:
    sec, _, knob = key.partition(".")
    try:
        return getattr(getattr(cfg, sec), knob)
    except AttributeError:
        return None


def config_items(changes: list[UM.Change], cfg: Config) -> list[dict[str, Any]]:
    """The knob changes that reach this project, judged against its config (see the module
    docstring). A knob added that the project already sets needs nothing and is left out."""
    items: list[dict[str, Any]] = []
    for key, (kind, c, old, new) in sorted(_net(changes).items()):
        source = cfg.sources.get(key, "default")
        if kind == "knob_removed":
            if (
                key not in cfg.unknown_knobs
                and f"[{key.partition('.')[0]}]" not in cfg.unknown_knobs
            ):
                continue  # the project never carried it
            # A removed knob is unknown to the schema, so it has no source; its being in
            # the config at all is what says someone wrote it.
            source = "config"
        operator_set = source != "default"
        if kind == "knob_added" and operator_set:
            continue
        item: dict[str, Any] = {
            "category": "config",
            "id": f"{kind}:{key}",
            "key": key,
            "change": kind,
            "old": old,
            "new": new,
            "set_by": "" if not operator_set else source,
            "why": c.why,
            "effect": c.effect,
            "action": OPERATOR if operator_set else (NOTE if kind == "knob_added" else AGENT),
        }
        if kind == "knob_added":
            item["summary"] = f"new knob {key} (default {_short(new)}): {c.why}".rstrip(": ")
        elif kind == "knob_changed":
            now = (
                f"; this project sets {_short(_current(cfg, key))} ({source})"
                if operator_set
                else ""
            )
            item["summary"] = f"default of {key} changed {_short(old)} -> {_short(new)}{now}" + (
                f": {c.effect}" if c.effect else ""
            )
        else:
            item["summary"] = f"knob {key} was removed; this project's config still sets it"
        item["fix"] = (
            f"ddflow upgrade --apply --confirm {key} --reason '<why>'{NOT_YET}"
            if operator_set
            else f"ddflow upgrade --apply{NOT_YET}"
        )
        items.append(item)
    return items


def feature_items(changes: list[UM.Change]) -> list[dict[str, Any]]:
    return [
        {
            "category": "features",
            "id": f"feature:{c.key}",
            "key": c.key,
            "summary": f"new opt-in feature {c.key}: {c.why}".rstrip(": "),
            "fix": c.enable,
            "action": NOTE,
        }
        for c in changes
        if c.kind == "feature"
    ]


def repair_items(repo: Path, log: Any, cfg: Config) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in RP.pending(RP.context(repo, log, cfg)):
        r = p.repair
        n = len(p.findings)
        if p.unavailable:
            summary = f"{r.title}: could not be checked ({p.unavailable})"
        else:
            summary = f"{r.title}: {n} finding(s); {r.action}"
        out.append(
            {
                "category": "repairs",
                "id": f"repair:{r.id}",
                "repair": r.id,
                "summary": summary,
                "findings": [{"key": f.key, "detail": f.detail} for f in p.findings[:_SHOWN]],
                "finding_count": n,
                "unavailable": p.unavailable,
                "action": OPERATOR if r.consent == RP.OPERATOR else AGENT,
                "fix": f"ddflow upgrade --apply{NOT_YET}",
            }
        )
    return out


def instruction_items(
    repo: Path, drifted_by_release: bool, docs_dir: str = "docs/ddflow"
) -> list[dict[str, Any]]:
    """Driver docs and rules files that differ from this ddflow's templates. ``drifted_by_release``
    is whether a release has shipped since the project last worked: only then can the drift
    be a stale copy; a project at the running version has EDITED the file."""
    kind = "stale" if drifted_by_release else "hand-edited"
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def add(path: str, state: str, text: str) -> None:
        if path in seen:
            return
        seen.add(path)
        out.append(
            {
                "category": "instructions",
                "id": f"instructions:{path}",
                "path": path,
                "state": state,
                "provenance": kind if state not in (AD.MISSING,) else "missing",
                "summary": text,
                "action": AGENT if kind == "stale" or state == AD.MISSING else OPERATOR,
                "fix": "ddflow adopt --refresh-docs",
            }
        )

    for rel in AD.driver_drift(repo, docs_dir=docs_dir):
        add(rel, "differs", f"{rel} differs from the one this ddflow ships ({kind})")
    for rs in AD.rules_status(repo, docs_dir=docs_dir):
        if rs.needs_attention:
            add(rs.path, rs.state, f"{rs.render()} ({kind})")
    return out


def hook_items(repo: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    mcp = LA.check_mcp(repo)
    for d in LA.findings(repo):
        if d in mcp:
            continue
        out.append(
            {
                "category": "hooks",
                "id": f"hooks:dangling:{d.where}",
                "summary": d.render(),
                "missing": list(d.missing),
                "action": AGENT,
                "fix": d.fix,
            }
        )
    adopted = set(AD.adopted_agents(repo))
    for h in CH.HOOKS:
        if h.agent not in adopted:
            continue
        on, why = CH.state_spec(repo, h)
        if on is None:
            # Not "installed" and not "missing": the settings file could not be read, and
            # nobody looked. Said, so an empty hooks list never hides a check that did not run.
            out.append(
                {
                    "category": "hooks",
                    "id": f"hooks:unknown:{h.agent}:{h.name}",
                    "summary": f"{h.agent} {h.event} hook could not be checked: {why}",
                    "action": NOTE,
                    "fix": "ddflow hooks status",
                }
            )
        elif on is False:
            out.append(
                {
                    "category": "hooks",
                    "id": f"hooks:missing:{h.agent}:{h.name}",
                    "summary": f"{h.agent} {h.event} hook is not installed: {h.purpose}",
                    "action": AGENT,
                    "fix": f"ddflow hooks install {h.flag}",
                }
            )
    return out


def mcp_items(repo: Path) -> list[dict[str, Any]]:
    return [
        {
            "category": "mcp",
            "id": f"mcp:dangling:{d.where}",
            "summary": d.render(),
            "missing": list(d.missing),
            "action": AGENT,
            "fix": d.fix,
        }
        for d in LA.check_mcp(repo)
    ]


def build(
    repo: Path,
    log: Any,
    cfg: Config,
    st: State,
    *,
    running: str = "",
    manifest: UM.Manifest | None = None,
) -> dict[str, Any]:
    """The plan, as plain data: `categories` maps each name in `CATEGORIES` to its items."""
    running = running or running_version()
    manifest = manifest or UM.load()
    has_history = bool(st.ddflow_versions or st.upgrades or st.items or st.sessions)
    baseline = project_version(st, has_history, running)
    changes = _since(baseline, running, manifest)
    cats: dict[str, list[dict[str, Any]]] = {
        "repairs": repair_items(repo, log, cfg),
        "config": config_items(changes, cfg),
        "instructions": instruction_items(repo, not baseline or is_older(baseline, running)),
        "hooks": hook_items(repo),
        "mcp": mcp_items(repo),
        "features": feature_items(changes),
    }
    total = sum(len(v) for v in cats.values())
    return {
        "running": running,
        "project_version": baseline,
        "up_to_date": total == 0,
        "total": total,
        "categories": cats,
    }


_TITLES = {
    "repairs": "data repairs",
    "config": "config",
    "instructions": "instructions",
    "hooks": "hooks",
    "mcp": "MCP launch",
    "features": "opt-in features",
}


def render(plan: dict[str, Any]) -> str:
    """The plan as text, one block per category that has anything to say."""
    head = (
        f"ddflow {plan['running']}; this project last worked under "
        f"{plan['project_version'] or 'a version before stamps'}"
    )
    if plan["up_to_date"]:
        return f"{head}\nUp to date: nothing to upgrade."
    lines = [head, f"{plan['total']} upgrade item(s) pending (plan only; nothing was written):", ""]
    for name in CATEGORIES:
        items = plan["categories"].get(name) or []
        if not items:
            continue
        lines.append(f"{_TITLES[name]} ({len(items)})")
        for it in items:
            lines.append(f"  - [{it['action']}] {it['summary']}")
            for f in it.get("findings", []):
                lines.append(f"      {f['detail']}")
            if it.get("finding_count", 0) > len(it.get("findings", [])):
                lines.append(f"      ... and {it['finding_count'] - len(it['findings'])} more")
            if it.get("fix"):
                lines.append(f"      -> {it['fix']}")
        lines.append("")
    lines.append("Companions are checked by `ddflow companions`.")
    return "\n".join(lines)
