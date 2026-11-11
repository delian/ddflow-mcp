"""The driver documents `adopt` writes and `refresh_docs` manages."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from ...core import clock
from ...infra import paths
from ...infra.fsio import Managed, RegionError, atomic_write, replace_text
from ..backups import make_backup
from ._native import _write_native_rule
from ._refused import Refused
from ._rules import adopted_agents, has_been_adopted, project_section, rules_status
from ._targets import NATIVE_RULES
from ._upsert import _upsert_block, local_edits_path


def _driver_pairs(repo: Path, docs_dir: str, templates: Path) -> list[tuple[str, Path, Path]]:
    """(repo-relative path, the project's copy, the template) for each driver doc that EXISTS.

    `implement-phase.md` plus every delta already present that this ddflow ships a template
    for: the deltas on disk are the record of which agents the project was adopted for, so
    a refresh never adds one the operator did not adopt.
    """
    src = templates / "drivers"
    dst = Path(repo) / docs_dir / "drivers"
    pairs = [
        (
            f"{docs_dir}/drivers/implement-phase.md",
            dst / "implement-phase.md",
            src / "implement-phase.md",
        )
    ]
    present = sorted((dst / "deltas").glob("*.md")) if (dst / "deltas").is_dir() else []
    for path in present:
        tmpl = src / "deltas" / path.name
        if tmpl.is_file():
            pairs.append((f"{docs_dir}/drivers/deltas/{path.name}", path, tmpl))
    return pairs


#: What a driver doc is, against the template this ddflow ships (`driver_states`).
DOC_CURRENT = "current"
#: Differs and is a copy an older ddflow wrote, unedited (its stamp proves it).
DOC_STALE = "stale"
#: Differs and has no stamp to tell by: a copy from before headers, edited or not.
DOC_LEGACY = "legacy"
#: Its stamped body no longer matches the digest it was written with: a person changed it.
DOC_EDITED = "edited"
#: Written at a higher format level than this ddflow understands (D-compat 2).
DOC_NEWER = "newer"


def _driver_region(rel: str) -> Managed:
    """The managed region a driver doc is, named by its path under `drivers/`."""
    name = rel.partition("/drivers/")[2] or Path(rel).name
    return Managed("drivers/" + name.removesuffix(".md"))


def doc_state(text: str, region: Managed, want: str) -> str:
    """How a managed document compares with ``want``, the body this ddflow ships.

    A file with the region: its body against ``want`` (current), else the stamp -- newer
    format, edited since it was written, or an unedited copy of an older release (stale).
    A file without one is a copy an older ddflow made: current when it is ``want`` byte for
    byte, else legacy (nothing says whether a person touched it, so the caller backs it up)."""
    try:
        at = region._region().find(text)
    except RegionError:
        return DOC_EDITED  # broken markers: somebody edited around them
    if at is None:
        return DOC_CURRENT if text == want else DOC_LEGACY
    body = text[at[1] : at[2]]
    state = region.state(text)
    if state == "newer":
        return DOC_NEWER
    if body == want:
        return DOC_CURRENT
    return DOC_EDITED if state == "edited" else DOC_STALE


def driver_states(
    repo: Path, *, docs_dir: str = "docs/ddflow", package_dir: Path | None = None
) -> dict[str, str]:
    """`{repo-relative path: DOC_*}` for each driver doc that exists and differs from, or is
    not exactly, the template the RUNNING ddflow ships; documents that are current are
    left out. Empty for a project that was never adopted."""
    if not has_been_adopted(repo, docs_dir=docs_dir):
        return {}
    templates = Path(package_dir) / "templates" if package_dir else paths.templates_dir()
    if not (templates / "drivers" / "implement-phase.md").is_file():
        return {}  # a packaging fault `adopt` names; doctor must not crash on it
    out: dict[str, str] = {}
    for rel, mine, tmpl in _driver_pairs(repo, docs_dir, templates):
        state = doc_state(
            mine.read_text("utf-8", errors="replace"),
            _driver_region(rel),
            tmpl.read_text("utf-8"),
        )
        if state != DOC_CURRENT:
            out[rel] = state
    return out


def driver_drift(
    repo: Path, *, docs_dir: str = "docs/ddflow", package_dir: Path | None = None
) -> list[str]:
    """Driver docs that differ from the templates the RUNNING ddflow ships.

    `rules_status` judges the AGENTS.md block; nothing judged the driver docs themselves,
    so a project adopted by an older ddflow kept a driver that lacked later guidance while
    doctor said Healthy (bug Ba11a054309). Empty for a project that was never adopted.
    """
    return list(driver_states(repo, docs_dir=docs_dir, package_dir=package_dir))


def _plan_driver(mine: Path, tmpl: Path, rel: str) -> str:
    """What `_write_driver` would do to one driver doc, predicted without writing it."""
    region, want = _driver_region(rel), tmpl.read_text("utf-8")
    if not mine.exists():
        return f"would create {rel}"
    try:
        existing = mine.read_text("utf-8")
    except UnicodeDecodeError:
        return f"would write {rel} (your file is not UTF-8: it is kept aside first)"
    state = doc_state(existing, region, want)
    if state == DOC_NEWER:
        s = region.stamp(existing)
        return (
            f"would refuse {rel}: written by ddflow {s.version if s else '?'} at a newer "
            "format level"
        )
    if state == DOC_CURRENT:
        return f"{rel} is current"
    note = ""
    if state == DOC_EDITED:
        note = " (your edits are kept in .local-edits)"
    elif state == DOC_LEGACY:
        note = " (a copy from before headers: nothing says whether it was edited)"
    return f"would rewrite {rel}{note}"


def _plan_actions(
    repo: Path, docs_dir: str, templates: Path, only: Collection[str] | None
) -> list[str]:
    """What `refresh_docs` would write, reported as a plan — nothing is written, no backup is
    made and no `.local-edits` copy is taken."""

    out: list[str] = []
    for rel, mine, tmpl in _driver_pairs(repo, docs_dir, templates):
        if only is not None and rel not in only:
            continue
        out.append(_plan_driver(mine, tmpl, rel))
    section = project_section(docs_dir)
    for name in ("AGENTS.md", "CLAUDE.md"):
        if only is not None and name not in only:
            continue
        path = repo / name
        if name == "CLAUDE.md" and not path.exists() and "claude" not in (
            adopted_agents(repo, docs_dir=docs_dir)
        ):
            continue
        out.append(_plan_block(path, section, name))
    for key in adopted_agents(repo, docs_dir=docs_dir):
        if key in NATIVE_RULES and (only is None or NATIVE_RULES[key].path in only):
            out.append(_plan_native(repo, key, docs_dir))
    return out


def _plan_block(path: Path, section: str, name: str) -> str:
    """Predict what `_upsert_block` would do to one rules file, without writing it."""
    label = name or path.name
    try:
        existing = path.read_text("utf-8") if path.exists() else ""
    except UnicodeDecodeError:
        return f"would skip {label}: it is not UTF-8 text"
    return f"would refresh the managed block in {label}"


def _plan_native(repo: Path, key: str, docs_dir: str) -> str:
    """Predict what `_write_native_rule` would do for one adopted agent, without writing."""
    return f"would refresh the native rule for {key}"


def _write_driver(mine: Path, tmpl: Path, rel: str) -> str:
    """Write one driver doc as a stamped region, keeping what a person wrote: an EDITED
    document is copied to `.local-edits` first, text outside the region stays, and one a newer
    ddflow wrote is refused."""
    region, want = _driver_region(rel), tmpl.read_text("utf-8")
    if not mine.exists():
        mine.parent.mkdir(parents=True, exist_ok=True)
        replace_text(mine, region.render(want))
        return f"wrote {rel}"
    try:
        existing = mine.read_text("utf-8")
    except UnicodeDecodeError:
        # Not text this ddflow can read back: keep the bytes whole and write the shipped doc.
        aside = local_edits_path(mine)
        atomic_write(aside, mine.read_bytes())
        replace_text(mine, region.render(want))
        return f"wrote {rel} (your file was not UTF-8: it is kept as {aside.name})"
    state = doc_state(existing, region, want)
    if state == DOC_NEWER:
        s = region.stamp(existing)
        return Refused(
            f"REFUSED {rel}: written by ddflow {s.version if s else '?'} at a newer format "
            f"level; upgrade ddflow to >= {s.version if s else 'that version'} before refreshing it"
        )
    note = ""
    if state == DOC_EDITED:
        aside = local_edits_path(mine)
        atomic_write(aside, mine.read_bytes())
        note = f" (your edits are kept in {aside.name})"
    try:
        owned = region.owns(existing)
    except RegionError:
        owned = False
    if state == DOC_CURRENT and owned:
        return f"{rel} is current"
    replace_text(mine, region.splice(existing, want) if owned else region.render(want))
    if state == DOC_LEGACY:
        note = " (a copy from before headers: nothing says whether it was edited; see the backup)"
    return f"wrote {rel}{note}"


def _backup_docs(
    repo: Path, docs_dir: str, package_dir: Path | None, only: Collection[str] | None
) -> str:
    """Copy aside the agent-facing documents a refresh is about to rewrite; the directory,
    or "" when none differs (a no-op refresh leaves no backup)."""
    stale = driver_drift(repo, docs_dir=docs_dir, package_dir=package_dir)
    stale += [r.path for r in rules_status(repo, docs_dir=docs_dir) if r.needs_attention]
    files = [Path(repo) / rel for rel in dict.fromkeys(stale) if only is None or rel in only]
    files = [f for f in files if f.is_file()]  # a file about to be CREATED has no original
    if not files:
        return ""
    return str(make_backup(repo, files, "", "", name=f"{clock.compact_at()}-refresh-docs"))


def refresh_docs(
    repo: Path,
    *,
    docs_dir: str = "docs/ddflow",
    package_dir: Path | None = None,
    only: Collection[str] | None = None,
    backup: bool = True,
    plan: bool = False,
) -> list[str]:
    """Rewrite ONLY the agent-facing documents: the driver docs, the managed rules blocks
    and the adopted agents' native rules. Never the MCP launch, the hooks, the command
    files, `.gitignore`/`.gitattributes` or `.ddflow/` -- the parts of a plain `adopt` an
    operator may have tuned by hand and that a docs refresh has no business touching.

    ``only`` limits the refresh to those repo-relative paths (the upgrade apply step
    refreshes the files its plan listed and leaves a hand-edited one it must not touch).
    Before a file is rewritten its original is copied to `.ddflow/backups/` (``backup``;
    `ddflow upgrade --apply` makes its own backup and passes False), and the first action
    says where.

    ``plan`` reports what a refresh would write without writing it (a diff mode): the
    actions describe each driver doc, rules file and native rule, nothing is rewritten, no
    backup is made and no `.local-edits` copy is taken.

    Refuses (ValueError) a project that was never adopted: a refresh must not adopt.
    """
    repo = Path(repo)
    if not has_been_adopted(repo, docs_dir=docs_dir):
        raise ValueError(
            f"{repo} has not been adopted (no {docs_dir}/drivers/implement-phase.md); "
            "run `ddflow adopt` first"
        )
    templates = Path(package_dir) / "templates" if package_dir else paths.templates_dir()
    if not (templates / "drivers" / "implement-phase.md").is_file():
        raise FileNotFoundError(
            f"driver templates are missing from {templates}. This is a packaging fault, "
            f"not a configuration one: reinstall ddflow-mcp."
        )
    if plan:
        return _plan_actions(repo, docs_dir, templates, only)
    actions: list[str] = []
    agents = adopted_agents(repo, docs_dir=docs_dir)
    if backup:
        saved = _backup_docs(repo, docs_dir, package_dir, only)
        if saved:
            actions.append(f"saved the originals in {saved}")
    for rel, mine, tmpl in _driver_pairs(repo, docs_dir, templates):
        if only is not None and rel not in only:
            continue
        actions.append(_write_driver(mine, tmpl, rel))
    section = project_section(docs_dir)
    for name in ("AGENTS.md", "CLAUDE.md"):
        if only is not None and name not in only:
            continue
        path = repo / name
        if name == "CLAUDE.md" and not path.exists() and "claude" not in agents:
            continue
        actions.append(_upsert_block(path, section, name))
    for key in agents:
        if key in NATIVE_RULES and (only is None or NATIVE_RULES[key].path in only):
            actions.append(_write_native_rule(repo, key, docs_dir))
    return actions
