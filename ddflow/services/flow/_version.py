"""PR flow: version planning and release notes.

Part of the former ``ddflow/services/flow.py``, split into submodules that stay under
30k chars each (rule r-small-files); see the package docstring for the full story.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ...config import Config
from ...core import flow as F
from ...core.model import DONE, Item, State
from ...infra import worktree as W
from ._reached import reached


@dataclass
class VersionPlan:
    ref: str
    current: str = ""
    current_tag: str = ""
    next: str = ""
    bump: str = ""
    reasons: list[str] = field(default_factory=list)
    commits: int = 0
    items: list[str] = field(default_factory=list)
    notes: str = ""
    problems: list[str] = field(default_factory=list)
    line: str = ""


def release_source(repo: Path, cfg: Config, line: str = "") -> str:
    """The branch a release is cut FROM: a maintenance line's branch, develop under
    gitflow, else the base branch."""
    if F.is_maintenance(cfg, line):
        return cfg.flow.lines[line]
    if cfg.flow.model == F.GITFLOW:
        return cfg.flow.develop_branch
    return cfg.worktree.base_ref or W.default_branch(repo)


def _ref(repo: Path, cfg: Config, branch: str) -> str:
    """Local branch, or the remote's copy of it in PR mode -- where merges happen."""
    if cfg.flow.integration == "pr":
        remote = cfg.flow.remote
        if W.fetch(repo, remote, branch).ok and W.rev(repo, f"{remote}/{branch}"):
            return f"{remote}/{branch}"
    return branch


def _set_next(vp: VersionPlan, cfg: Config, version: str, line: str) -> None:
    """The next version: given, else bumped from the current one, else the initial one."""
    if version:
        if F.parse_version(version) is None:
            vp.problems.append(f"{version!r} is not MAJOR.MINOR.PATCH")
            vp.next = version
            return
        if vp.current and F.parse_version(version) <= F.parse_version(vp.current):
            vp.problems.append(f"{version} is not above the current {vp.current}")
        vp.next = version
    elif not vp.commits:
        vp.next = ""
    elif not vp.current:
        vp.next = cfg.flow.initial_version
    else:
        vp.next = F.fmt(F.bump(F.parse_version(vp.current), vp.bump))
    if (
        F.is_maintenance(cfg, line)
        and vp.current
        and vp.next
        and F.parse_version(vp.next)[0] != F.parse_version(vp.current)[0]
    ):
        # A maintenance line exists to KEEP its major. A breaking change released from it
        # would claim the next major -- which a newer line already owns.
        vp.problems.append(
            f"{vp.next} leaves the {vp.current.split('.')[0]}.x major, and line {line!r} is a "
            f"maintenance line: a breaking change belongs on the current line. Release "
            f"with --bump minor or --bump patch, or --version."
        )


def plan_version(
    repo: Path, cfg: Config, st: State, *, bump: str = "", version: str = "", line: str = ""
) -> VersionPlan:
    branch = release_source(repo, cfg, line)
    ref = _ref(repo, cfg, branch)
    vp = VersionPlan(ref=ref)
    vp.problems = F.problems(cfg)
    if line and line not in F.line_order(cfg):
        vp.problems.append(f"unknown release line {line!r}; lines: {', '.join(F.line_order(cfg))}")
        return vp
    if not W.rev(repo, ref):
        vp.problems.append(f"release source {ref!r} does not exist")
        return vp
    vp.current_tag, vp.current = W.reachable_tag(repo, cfg.flow.tag_prefix, ref)
    messages = W.log_messages(repo, vp.current_tag, ref)
    vp.commits = len(messages)
    bumps: list[str] = []
    for m in messages:
        b = F.commit_bump(m)
        if b:
            bumps.append(b)
            vp.reasons.append(f"{b}: {m.splitlines()[0][:80]}")
    # Released ON THIS LINE. A fix forward-merged from 1.x to 3.x ships in both a 1.x and
    # a 3.x release; excluding it everywhere once either was cut dropped it from the other
    # line's notes and bump (RESEARCH R17 review).
    here = line or cfg.flow.current_line
    released = {
        i for r in st.releases if (r.line or cfg.flow.current_line) == here for i in r.items
    }
    shipped: list[Item] = []
    for it in sorted(st.items.values(), key=lambda i: i.id):
        if it.removed or it.state != DONE or not it.is_work or it.id in released:
            continue
        sha = reached(repo, cfg, it, ref)
        if not sha:
            continue
        if vp.current_tag and W.git(repo, "merge-base", "--is-ancestor", sha, vp.current_tag).ok:
            continue
        shipped.append(it)
        b = F.item_bump(it, cfg)
        if b:
            bumps.append(b)
            vp.reasons.append(f"{b}: item {it.id} ({', '.join(it.tags)})")
    vp.items = [i.id for i in shipped]
    vp.bump = bump or F.strongest(bumps) or (F.PATCH if vp.commits else "")
    _set_next(vp, cfg, version, line)
    vp.line = line or cfg.flow.current_line
    vp.notes = release_notes(vp, shipped, messages, cfg)
    return vp


def release_notes(vp: VersionPlan, shipped: list[Item], messages: list[str], cfg: Config) -> str:
    groups: dict[str, list[str]] = {F.MAJOR: [], F.MINOR: [], F.PATCH: [], "": []}
    for it in shipped:
        link = f" ({it.pr.url})" if it.pr and it.pr.url else ""
        groups[F.item_bump(it, cfg)].append(f"- {it.title or it.id} [{it.id}]{link}")
    for m in messages:
        first = m.splitlines()[0]
        if "Item:" in m or first.startswith("Merge "):
            continue  # covered by the item line, or a merge commit's boilerplate
        b = F.commit_bump(m)
        if b:
            groups[b].append(f"- {first}")
    heads = {F.MAJOR: "Breaking changes", F.MINOR: "Features", F.PATCH: "Fixes", "": "Other"}
    out = [f"## {cfg.flow.tag_prefix}{vp.next or '?'}", ""]
    for key in (F.MAJOR, F.MINOR, F.PATCH, ""):
        if groups[key]:
            out += [f"### {heads[key]}", *groups[key], ""]
    return "\n".join(out).rstrip() + "\n"
