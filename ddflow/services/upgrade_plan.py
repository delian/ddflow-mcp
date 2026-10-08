"""`ddflow upgrade --plan`: what upgrading this project to the running ddflow would change.

Decision D-upgrade-model (3), task B-upgrade.3-plan. The plan only READS: it asks the pieces
that already know (the data repairs, the upgrade manifest, config source tracking, the
driver/rules checks, the hook and launcher checks) and lays their answers side by side in
one list, grouped by CATEGORY:

- `repairs`: data damage an older ddflow left in the log that a repair would mend;
- `migrations`: breaking changes of a release to something the project holds (a file format, a
  marker grammar, a reference), each a registered migration (`services.migrations`) that
  detects, plans (the files it would rewrite) and, on apply, backs those up first;
- `config`: knobs added, knobs whose default changed and knobs removed since the version this
  project last worked under (the manifest, `services.upgrade_manifest`), judged against THIS
  project's config -- a knob it never set takes the new default; one anyone set (a config
  layer, the environment, `ddflow config`) is the operator's and is never changed without
  their confirmation (decision D-upgrade-config-changes);
- `instructions`: driver docs, rules blocks and native rules whose bytes differ from the ones
  this ddflow ships, and whether the drift is a release's (`stale`) or an edit (`hand-edited`);
  also a note per file whose OWN text names a deprecated command or tool (never edited for
  you: the `stale-references` migration rewrites only what ddflow wrote);
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
from . import migrations as MG
from . import repairs as RP
from . import upgrade_manifest as UM
from .compat_refs import DEPRECATED, scan
from .migrations import refs as MR

CATEGORIES = ("repairs", "migrations", "config", "instructions", "hooks", "mcp", "features")

AGENT = "agent"
OPERATOR = "needs operator confirmation"
NOTE = "note"

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
    """Per knob, what the releases since the baseline add up to: `(kind, last change, old,
    new)`. The FIRST change says whether the knob existed at the baseline (a change or a
    removal: it did, holding that `old`; an addition: it did not) and the LAST where it
    ended (removed, or holding that `new`). So added then changed is an addition with the
    last default, added then removed never reached the project, removed then added back is
    a changed default, and a knob changed back to what it was is no change."""
    chain: dict[str, list[UM.Change]] = {}
    for c in changes:
        if c.kind.startswith("knob_"):
            chain.setdefault(c.key, []).append(c)
    out: dict[str, tuple[str, UM.Change, Any, Any]] = {}
    for key, cs in chain.items():
        first, last = cs[0], cs[-1]
        existed = first.kind != "knob_added"
        if last.kind == "knob_removed":
            if existed:
                out[key] = ("knob_removed", last, first.old, None)
        elif not existed:
            out[key] = ("knob_added", last, None, last.new)
        elif first.old != last.new:
            out[key] = ("knob_changed", last, first.old, last.new)
    return out


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
            "action": OPERATOR
            if operator_set or (kind != "knob_added" and cfg.upgrade.config_changes == "operator")
            else (NOTE if kind == "knob_added" else AGENT),
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
            f"ddflow upgrade --apply --confirm {key} --reason '<why>'"
            if operator_set
            else "ddflow upgrade --apply"
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
                "fix": "ddflow upgrade --apply",
            }
        )
    return out


def migration_items(repo: Path, log: Any, cfg: Config, running: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in MG.pending(MG.context(repo, log, cfg), running=running):
        m = p.migration
        n = len(p.findings)
        if p.unavailable:
            summary = f"{m.title}: could not be checked ({p.unavailable})"
        else:
            summary = f"{m.title}: {n} finding(s); {m.action}"
        out.append(
            {
                "category": "migrations",
                "id": f"migration:{m.id}",
                "migration": m.id,
                "since_version": m.since_version,
                "format_level": m.format_level,
                "kinds": list(m.kinds),
                "summary": summary,
                "findings": [{"key": f.key, "detail": f.detail} for f in p.findings[:_SHOWN]],
                "finding_count": n,
                "changes": [{"path": c.path, "action": c.action} for c in p.changes],
                "paths": sorted({c.path for c in p.changes}),
                "unavailable": p.unavailable,
                "action": OPERATOR if m.consent == MG.OPERATOR else AGENT,
                "fix": "ddflow upgrade --apply migrations",
            }
        )
    return out


def _provenance(by_release: bool, *, edited: bool, proven: bool) -> tuple[str, str]:
    """`(provenance, action)` for a differing instruction file. A stamped copy PROVES what it
    is: edited since ddflow wrote it, or an unedited older release; both are safe for an
    agent (a refresh keeps an edit in `.local-edits`). A copy with no stamp is judged by
    whether a release has shipped since the project last worked: only then can the drift be a
    stale copy, else somebody edited it and the operator decides."""
    if edited:
        return "hand-edited", AGENT
    if proven or by_release:
        return "stale", AGENT
    return "hand-edited", OPERATOR


def _newer_item(path: str, text: str) -> dict[str, Any]:
    return {
        "category": "instructions",
        "id": f"instructions:{path}",
        "path": path,
        "state": "newer",
        "provenance": "newer",
        "summary": f"{text}: upgrade ddflow, do not refresh it",
        "action": NOTE,
        "fix": "upgrade ddflow",
    }


def instruction_items(
    repo: Path, drifted_by_release: bool, docs_dir: str = "docs/ddflow"
) -> list[dict[str, Any]]:
    """Driver docs and rules files that differ from this ddflow's templates. ``drifted_by_release``
    is whether a release has shipped since the project last worked: it settles only the files
    that carry no stamp (see `_provenance`)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def add(
        path: str, state: str, text: str, provenance: str, action: str, *, kept: bool = False
    ) -> None:
        if path in seen:
            return
        seen.add(path)
        missing = state == AD.MISSING
        keeps = " (a refresh keeps your edits in .local-edits)" if kept and not missing else ""
        out.append(
            {
                "category": "instructions",
                "id": f"instructions:{path}",
                "path": path,
                "state": state,
                "provenance": provenance if state != AD.MISSING else "missing",
                "summary": text if missing else f"{text} ({provenance}){keeps}",
                "action": AGENT if state == AD.MISSING else action,
                "fix": "ddflow adopt --refresh-docs",
            }
        )

    for rel, st in AD.driver_states(repo, docs_dir=docs_dir).items():
        if st == AD.DOC_NEWER:
            # Written by a newer ddflow: nothing this one may do (D-compat 2).
            seen.add(rel)
            out.append(_newer_item(rel, f"{rel} was written by a newer ddflow"))
            continue
        prov, action = _provenance(
            drifted_by_release, edited=st == AD.DOC_EDITED, proven=st == AD.DOC_STALE
        )
        add(
            rel,
            "differs",
            f"{rel} differs from the one this ddflow ships",
            prov,
            action,
            kept=st == AD.DOC_EDITED,
        )
    for rs in AD.rules_status(repo, docs_dir=docs_dir):
        if not rs.needs_attention:
            continue
        if rs.newer:
            seen.add(rs.path)
            out.append(_newer_item(rs.path, f"{rs.path}'s block was written by a newer ddflow"))
            continue
        prov, action = _provenance(
            drifted_by_release, edited=rs.edited, proven=rs.stamped and rs.state == AD.STALE
        )
        add(rs.path, rs.state, rs.render(), prov, action, kept=rs.edited and rs.stamped)
    return out


def reference_items(repo: Path) -> list[dict[str, Any]]:
    """One note per file whose OWN text (not a region ddflow wrote) names a deprecated command
    or tool. An upgrade never edits it: the plan says what to change, the alias keeps the old
    name working until 1.0. (What ddflow wrote itself is rewritten by the `stale-references`
    migration.)"""
    vocab = MR._vocabulary()
    if vocab is None:
        return []  # nothing loaded to judge names by: nothing is reported (as `doctor` does)
    by_path: dict[str, list[Any]] = {}
    for f in scan(repo, vocab):
        if not f.managed and f.ref.status == DEPRECATED and f.ref.replacement:
            by_path.setdefault(f.path, []).append(f)
    out: list[dict[str, Any]] = []
    for path, found in sorted(by_path.items()):
        out.append(
            {
                "category": "instructions",
                "id": f"instructions:references:{path}",
                "path": path,
                "state": "references",
                "provenance": "yours",
                "summary": f"{path} names {len(found)} deprecated ddflow name(s) in your own text",
                "findings": [{"key": f.where, "detail": f.proposal} for f in found[:_SHOWN]],
                "finding_count": len(found),
                "action": NOTE,
                "fix": "edit them yourself; the old names keep working until 1.0",
            }
        )
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
                "paths": [d.path] if d.path else [],
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
                    "paths": [h.file],
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
            "paths": [d.path] if d.path else [],
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
        "migrations": migration_items(repo, log, cfg, running),
        "config": config_items(changes, cfg),
        "instructions": instruction_items(repo, not baseline or is_older(baseline, running))
        + reference_items(repo),
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
    "migrations": "migrations",
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
            for c in it.get("changes", []):
                lines.append(f"      would: {c['path']}: {c['action']}")
            if it.get("finding_count", 0) > len(it.get("findings", [])):
                lines.append(f"      ... and {it['finding_count'] - len(it['findings'])} more")
            if it.get("fix"):
                lines.append(f"      -> {it['fix']}")
        lines.append("")
    lines.append("Companions are checked by `ddflow companions`.")
    return "\n".join(lines)
