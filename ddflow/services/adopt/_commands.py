"""Slash-command files adopt writes for harnesses whose loop primitive takes one, and the
managed regions they are written into."""

from __future__ import annotations

from pathlib import Path

from ...core.digest import content_digest
from ...infra.fsio import (
    FORMAT_LEVEL,
    MANAGED_DIGEST_LEN,
    Managed,
    NewerContent,
    RegionError,
    atomic_write,
    replace_text,
)
from .. import harnessreg as HR
from ._refused import Refused
from ._upsert import local_edits_path


def _install_prompt_hooks(repo: Path, agents: list[str]) -> list[str]:
    """The prompt-capture hook for each adopted agent that has one (Claude Code, Gemini CLI).

    A settings file ddflow cannot parse is reported, not overwritten, and never fails the
    adoption.
    """
    # Deferred: claudehooks imports enforce, which the engine must not load.
    from .. import claudehooks as CH

    out: list[str] = []
    for h in CH.HOOKS:
        if h.name != "prompt" or h.agent not in agents:
            continue
        try:
            out.append(CH.install_spec(repo, h))
        except CH.SettingsError as exc:
            out.append(f"prompt hook not installed: {exc}")
    return out


#: Slash-command files an agent reads from the project, keyed like `AGENT_TARGETS`:
#: {repo-relative destination: source under `templates/`}. The MCP prompt of the same name
#: already reaches every client; this is for a harness whose loop primitive (Claude Code's
#: `/loop`) takes a slash command, so `/implement` can hand the workflow to it unattended.
AGENT_COMMANDS: dict[str, dict[str, str]] = {
    h.id: dict(h.commands.files) for h in HR.load() if h.commands is not None and h.commands.files
}

#: Subagent definition files to emit for a harness, keyed and shaped like `AGENT_COMMANDS`
#: (`{repo-relative destination: source under templates/}`). The generic mirror of the
#: Claude-specific lean role definitions in `services.leanagents`: a harness whose
#: `[subagents].files` names its own subagent surfaces (Kilo's `.kilo/agent/`).
AGENT_SUBAGENTS: dict[str, dict[str, str]] = {
    h.id: dict(h.subagents.files)
    for h in HR.load()
    if h.subagents is not None and not h.subagents.none and h.subagents.files
}


def _write_harness_files(repo: Path, key: str, templates: Path) -> list[str]:
    """The slash-command and subagent files a harness reads, written from its descriptor's
    `[commands].files` and `[subagents].files` (one action line each)."""
    out = [
        _write_command(repo, dst, templates / src)
        for dst, src in AGENT_COMMANDS.get(key, {}).items()
    ]
    out += [
        _write_command(repo, dst, templates / src, "agents")
        for dst, src in AGENT_SUBAGENTS.get(key, {}).items()
    ]
    return out


#: The line that marked a command file as ddflow's own before the stamped grammar. A file
#: carrying it is still ours (adopt migrates it to the region below); a file without it, or
#: with a region someone broke, is the operator's -- a project adopting mid-stream often has
#: an `/implement` of its own already, and overwriting it would destroy the one workflow the
#: project actually runs. To keep your own: delete the begin line.
MANAGED_MARK = "<!-- DDFLOW:MANAGED"


def _command_region(rel: str, kind: str = "commands") -> Managed:
    return Managed(f"{kind}/{Path(rel).stem}")


def _split_front_matter(text: str) -> tuple[str, str]:
    """`(front matter incl. its closing line, the rest)`: the managed region starts after
    the front matter so a slash command's `---` block stays first in the file."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 3)
        if end != -1:
            cut = end + len("\n---\n")
            return text[:cut], text[cut:]
    return "", text


def _region_body(region: Managed, text: str) -> str | None:
    at = region._region().find(text)
    return None if at is None else text[at[1] : at[2]]


def _head_stamp(head: str) -> tuple[tuple[str, str], ...]:
    """The region attribute that remembers which front matter ddflow wrote, so a later adopt can tell
    a front matter it may refresh from one the operator edited."""
    return (("head", content_digest(head, length=MANAGED_DIGEST_LEN)),)


def _refresh_head(region: Managed, existing: str, head: str) -> tuple[str, str]:
    """``(the front matter to keep or write, its state)`` for a managed file that exists: `same` as the
    template's; `refresh`, ddflow's own older one (its digest is the stamp's `head`), replaced quietly;
    `old`, a file from before the stamp (unknowable, so the old file is saved); `edited`, the operator's,
    which stays."""
    have = _split_front_matter(existing)[0]
    if have == head:
        return head, "same"
    stamp = region.stamp(existing)
    wrote = dict(stamp.extra).get("head") if stamp else None
    if wrote is None:
        return head, "old"
    if wrote == content_digest(have, length=MANAGED_DIGEST_LEN):
        return head, "refresh"
    return have, "edited"


def _update_managed(
    path: Path, rel: str, region: Managed, existing: str, head: str, body: str
) -> str:
    """The action line for a file that holds this region: current, refused (a newer ddflow wrote
    it), or rewritten with its body, and its front matter when ddflow's, refreshed."""
    use_head, head_state = _refresh_head(region, existing, head)
    have_head = _split_front_matter(existing)[0]
    if _region_body(region, existing) == body and head_state in ("same", "edited"):
        return f"{rel} is current" + (
            " (front matter edited by hand: kept)" * (head_state == "edited")
        )
    stamp = region.stamp(existing)
    if region.state(existing) == "newer" and stamp is not None:
        why = NewerContent(f"the {region.name} region", stamp.version, stamp.fmt)
        return Refused(f"REFUSED {rel}: {why}")
    # `head` is what ddflow wrote (not a kept edit); attributes this version does not know are carried over
    kept = tuple(
        kv for kv in (stamp.extra if stamp and stamp.fmt == FORMAT_LEVEL else ()) if kv[0] != "head"
    )
    block = region.render(body, extra=(*_head_stamp(head), *kept))
    updated = use_head + region._region().splice(existing[len(have_head) :], block)
    notes = []
    if head_state == "edited":
        notes.append("front matter edited by hand: kept")
    if region.state(existing) == "edited" or head_state == "old":
        aside = local_edits_path(path)  # a hand edit inside the region, or the pre-stamp file
        atomic_write(aside, path.read_bytes())
        which = "your edits are" if region.state(existing) == "edited" else "the previous file is"
        notes.append(f"{which} kept in {aside.name}")
    replace_text(path, updated)
    return f"wrote {rel}" + (f" ({'; '.join(notes)})" if notes else "")


def _write_command(repo: Path, rel: str, src: Path, kind: str = "commands") -> str:
    path = repo / rel
    head, body = _split_front_matter(src.read_text("utf-8"))
    region = _command_region(rel, kind)
    if path.exists() and not path.is_file():
        return Refused(f"SKIPPED {rel}: it exists and is not a file; move it and re-run adopt")
    if path.exists():
        existing = path.read_text("utf-8")
        try:
            owned = region.owns(existing)
        except RegionError:
            owned = False  # a broken region is the operator's file: leave it alone
        if owned:
            return _update_managed(path, rel, region, existing, head, body)
        if MANAGED_MARK not in existing:
            ours = "the MCP prompt `implement`" if kind == "commands" else "the template in ddflow"
            return (
                f"kept {rel}: it is the project's own, not ddflow's. The ddflow version is "
                f"{ours}. Delete the file and re-run adopt to take it"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_text(path, head + region.render(body, extra=_head_stamp(head)))
    return f"wrote {rel}"
