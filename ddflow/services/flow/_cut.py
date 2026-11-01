"""PR flow: cutting a version.

Part of the former ``ddflow/services/flow.py``, split into submodules that stay under
30k chars each (rule r-small-files); see the package docstring for the full story.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path

from ...config import Config
from ...core import flow as F
from ...core.model import DONE
from ...infra import forge as FG
from ...infra import worktree as W
from ...infra.log import EventLog
from ..export.query import EXIT_REFUSED, ExportError
from ._core import _state
from ._reached import reached
from ._report import Change, SyncReport
from ._version import VersionPlan, plan_version


@dataclass
class Cut:
    version: str = ""
    tag: str = ""
    sha: str = ""
    ok: bool = False
    refused: bool = False
    unavailable: bool = False
    nothing: bool = False
    reason: str = ""
    pushed: bool = False
    url: str = ""
    steps: list[str] = field(default_factory=list)
    plan: VersionPlan | None = None
    changelog: str = ""  # the changelog file the cut wrote (--changelog), "" when none
    version_files: list[str] = field(default_factory=list)  # files the cut bumped (B174)


def _cut_refusal(repo: Path, cfg: Config, st, vp: VersionPlan, out: Cut, bump: str) -> bool:
    """True once ``out`` records why nothing can be cut: a refused plan, nothing to
    release, a tag that exists, a release already awaiting review. Else it names the tag."""
    if bump and bump not in F.BUMPS:
        vp.problems.append(f"bump {bump!r} is not one of {', '.join(F.BUMPS)}")
    if vp.problems:
        out.refused, out.reason = True, "; ".join(vp.problems)
        return True
    if not vp.next:
        out.nothing = True
        out.reason = (
            f"nothing to release: no commits on {vp.ref} since {vp.current_tag or 'the start'}"
        )
        return True
    out.tag = f"{cfg.flow.tag_prefix}{vp.next}"
    if W.rev(repo, f"refs/tags/{out.tag}"):
        out.refused, out.reason = True, f"tag {out.tag} already exists"
        return True
    if vp.next in st.pending_releases:
        pending = st.pending_releases[vp.next]
        out.refused, out.reason = (
            True,
            f"release {vp.next} is already awaiting review: {pending.get('url')}",
        )
        return True
    return False


def cut(
    repo: Path,
    cfg: Config,
    log: EventLog,
    *,
    bump: str = "",
    version: str = "",
    push: bool = False,
    dry_run: bool = False,
    line: str = "",
    changelog: bool = False,
    force: bool = False,
) -> Cut:
    """Tag the next version. Under gitflow, via a release branch.

    trunk              tag the base branch head.
    gitflow + merge    release/X from develop -> merged into production -> tagged there
                       -> the tag merged back into develop, so develop's next version
                       is computed from a tag it actually contains.
    gitflow + pr       release/X is pushed and a request opened into production; the
                       tag is cut by `pr sync` when a person merges it.

    ``changelog`` also writes the version's section into CHANGELOG.md (``changelog_cut``),
    committed on the branch the tag names -- or, in pr mode, on the release branch the
    request carries. Without it nothing but the tag is written.
    """
    st = _state(log)
    vp = plan_version(repo, cfg, st, bump=bump, version=version, line=line)
    out = Cut(plan=vp, version=vp.next)
    if _cut_refusal(repo, cfg, st, vp, out, bump):
        return out
    direct = cfg.flow.model != F.GITFLOW or F.is_maintenance(cfg, line)
    planned, vfiles = _plan_version_files(repo, cfg, out, vp, direct=direct)
    if not planned:
        return out
    prep = None
    if changelog:
        if direct and cfg.flow.integration == "pr":
            out.refused, out.reason = (
                True,
                (
                    "--changelog with pull requests needs [flow].model = gitflow: a trunk or "
                    "maintenance cut tags the remote branch as it is, and the changelog commit "
                    "would have no request to travel in"
                ),
            )
            return out
        prep = _prepare_changelog(repo, cfg, out, vp, force=force, dry_run=dry_run)
        if prep is None:
            return out
    if dry_run:
        out.ok = True
        if vfiles:
            out.steps.append(f"dry run: would {vfiles.describe(vp.next)}")
        out.steps.append("dry run: nothing written")
        return out
    if direct:
        # A maintenance line is tagged where it stands: it has no develop/production
        # pair of its own, so there is no release branch to route through.
        # The changelog first, the bump last: a failed bump rolls its own working tree back,
        # but a changelog commit already made on the branch STAYS (a branch ref cannot be
        # reset safely when another worktree may have it checked out), so say so.
        before = W.rev(repo, vp.ref)
        if prep and not _write_changelog(repo, cfg, out, vp.ref, prep, vp.next, force=force):
            return out
        if vfiles and not _write_version_files(repo, cfg, out, vp.ref, vfiles, vp.next):
            _note_changelog_commit_stays(repo, out, vp.ref, before)
            return out
        out.sha = W.rev(repo, vp.ref)
        return _tag_and_push(repo, cfg, log, out, vp, branch=vp.ref, push=push)
    return _cut_gitflow(repo, cfg, log, out, vp, push=push, prep=prep, force=force, vfiles=vfiles)


def _note_changelog_commit_stays(repo: Path, out: Cut, ref: str, before: str) -> None:
    """A failed bump rolls its own tree back, but a changelog commit already made on the
    branch stays: say which commit, and how to remove it."""
    landed = W.rev(repo, ref)
    if landed and landed != before:
        out.steps.append(
            f"NOTE: the changelog commit {landed[:10]} was already made on {ref} and "
            f"stays: `git revert {landed[:10]}` removes it, or fix the bump and run "
            f"`ddflow version cut` again (the changelog section is then already there)"
        )


def _plan_version_files(repo: Path, cfg: Config, out: Cut, vp: VersionPlan, *, direct: bool):
    """``(ok, prepared)``: what `[flow.version_files]` will change, checked before anything
    is written. ``ok`` False means the cut is refused (the reason is on ``out``)."""
    if not cfg.flow.version_files:
        return True, None
    if direct and cfg.flow.integration == "pr":
        out.refused, out.reason = (
            True,
            (
                "[flow.version_files] with pull requests needs [flow].model = gitflow: a "
                "trunk or maintenance cut tags the remote branch as it is, and the bump "
                "commit would have no request to travel in"
            ),
        )
        return False, None
    prep = _prepare_version_files(repo, cfg, out, vp)
    return prep is not None, prep


def _prepare_version_files(repo: Path, cfg: Config, out: Cut, vp: VersionPlan):
    """The new text of every `[flow.version_files]` file, or None after recording why not."""
    # Late-bound (importlib): a module-level import reaches `reached` in this package while
    # it is still initialising (ImportError, an import cycle through changelog_cut).
    VF = importlib.import_module("..version_files", __package__)

    try:
        return VF.prepare(repo, cfg, version=vp.next, ref=vp.ref)
    except VF.VersionFileError as exc:
        out.refused, out.reason = True, str(exc)
        return None


def _write_version_files(
    repo: Path, cfg: Config, out: Cut, branch: str, prep, version: str
) -> bool:
    """Commit the bump on ``branch``; False (with the reason on ``out``) if it cannot be."""
    # Late-bound (importlib): a module-level import reaches `reached` in this package while
    # it is still initialising (ImportError, an import cycle through changelog_cut).
    VF = importlib.import_module("..version_files", __package__)

    try:
        changed = VF.commit_on(repo, cfg, branch, prep, message=f"chore: bump version to {version}")
    except VF.VersionFileError as exc:
        out.reason = str(exc)
        out.unavailable, out.refused = exc.unavailable, not exc.unavailable
        return False
    out.version_files = changed
    out.steps.append(
        f"bumped {', '.join(changed)} on {branch}" if changed else f"{prep.describe(version)}"
    )
    return True


def _export_failed(out: Cut, exc: Exception) -> None:
    """An export failure as the cut's outcome: exit 3 when ddflow refuses (a hand-edited
    file), exit 2 when it could not run (git, the log)."""
    out.ok = False
    out.reason = str(exc)

    if getattr(exc, "code", 0) == EXIT_REFUSED:
        out.refused = True
    else:
        out.unavailable = True


def _prepare_changelog(repo, cfg, out: Cut, vp: VersionPlan, *, force: bool, dry_run: bool):
    """The rendered changelog for the cut, or None after recording why not on ``out``.
    The version's section also becomes the tag message (the request body in pr mode, where
    `pr sync` writes the tag's message later)."""
    # Late-bound (importlib): a module-level import reaches `reached` in this package while
    # it is still initialising (ImportError, an import cycle).
    CC = importlib.import_module("..changelog_cut", __package__)

    try:
        prep = CC.prepare(repo, cfg, version=vp.next, ref=vp.ref, fallback_notes=vp.notes)
        if dry_run:
            prep.apply(W.repo_root(repo), force=force, dry=True)
    except ExportError as exc:
        _export_failed(out, exc)
        return None
    vp.notes = prep.notes
    if dry_run:
        out.steps.append(f"dry run: would write {prep.path} ({prep.mode})")
    return prep


def _write_changelog(repo, cfg, out: Cut, branch: str, prep, version: str, *, force: bool) -> bool:
    """Commit the prepared changelog on ``branch``; False (with the reason on ``out``) if not."""
    # Late-bound (importlib): a module-level import reaches `reached` in this package while
    # it is still initialising (ImportError, an import cycle).
    CC = importlib.import_module("..changelog_cut", __package__)

    try:
        action = CC.commit_on(
            repo, cfg, branch, prep, force=force, message=f"docs: changelog for {version}"
        )
    except ExportError as exc:
        _export_failed(out, exc)
        return False
    out.changelog = prep.path
    out.steps.append(f"{action} {prep.path} on {branch}")
    return True


def _cut_gitflow(
    repo: Path,
    cfg: Config,
    log: EventLog,
    out: Cut,
    vp: VersionPlan,
    *,
    push: bool,
    prep=None,
    force: bool = False,
    vfiles=None,
) -> Cut:
    remote = cfg.flow.remote
    prod = F.production(cfg, W.default_branch(repo))
    rel = f"{cfg.flow.release_prefix}{vp.next}"
    mk = W.git(repo, "branch", rel, vp.ref)
    if not mk.ok:
        out.refused, out.reason = True, f"could not create {rel}: {mk.err}"
        return out
    out.steps.append(f"created {rel} from {vp.ref}")
    if vfiles and not _write_version_files(repo, cfg, out, rel, vfiles, vp.next):
        W.git(repo, "branch", "-D", rel)  # unmade, as a failed push unmakes it
        return out
    if prep and not _write_changelog(repo, cfg, out, rel, prep, vp.next, force=force):
        W.git(repo, "branch", "-D", rel)  # unmade, as a failed push unmakes it
        return out
    if cfg.flow.integration == "pr":
        return _open_release(repo, cfg, log, out, vp, rel=rel, prod=prod)
    r = W.merge_into(repo, cfg, prod, rel, message=f"release {vp.next}")
    if not r.ok:
        out.refused, out.reason = True, f"merging {rel} into {prod} failed: {r.err or r.out}"
        return out
    out.steps.append(f"merged {rel} into {prod}")
    out.sha = W.rev(repo, prod)
    done = _tag_and_push(repo, cfg, log, out, vp, branch=prod, push=False)
    if not done.ok:
        return done
    back = W.merge_into(
        repo, cfg, cfg.flow.develop_branch, out.tag, message=f"back-merge {out.tag}"
    )
    if not back.ok:
        out.ok = False
        out.refused = True
        out.reason = (
            f"{out.tag} is tagged on {prod}, but merging it back into "
            f"{cfg.flow.develop_branch} failed: {back.err or back.out}. Resolve by hand; "
            f"until then develop's next version is computed from the previous tag."
        )
        return out
    out.steps.append(f"merged {out.tag} back into {cfg.flow.develop_branch}")
    W.git(repo, "branch", "-d", rel)
    if push:
        for ref in (prod, cfg.flow.develop_branch, out.tag):
            p = W.git(repo, "push", remote, ref)
            if not p.ok:
                out.reason = f"pushing {ref} failed: {p.err}"
                return out
        out.pushed = True
        out.steps.append(f"pushed {prod}, {cfg.flow.develop_branch} and {out.tag} to {remote}")
    return out


def _open_release(
    repo: Path, cfg: Config, log: EventLog, out: Cut, vp: VersionPlan, *, rel: str, prod: str
) -> Cut:
    remote = cfg.flow.remote
    pushed = W.push(repo, remote, rel)
    if not pushed.ok:
        # Unmade, so the next cut of this version is not refused by our own leftover.
        W.git(repo, "branch", "-D", rel)
        out.refused, out.reason = True, f"push of {rel} rejected: {pushed.err}"
        return out
    try:
        forge = FG.detect(repo, cfg)
        info = forge.find(rel) or forge.create(
            head=rel,
            base=prod,
            title=f"Release {vp.next}",
            body=vp.notes,
            draft=False,
            labels=list(cfg.flow.pr_labels),
            reviewers=list(cfg.flow.pr_reviewers),
        )
    except FG.ForgeUnavailable as exc:
        W.git(repo, "branch", "-D", rel)
        out.unavailable, out.reason = True, str(exc)
        return out
    except FG.ForgeError as exc:
        W.git(repo, "branch", "-D", rel)
        out.refused, out.reason = True, str(exc)
        return out
    log.append(
        "release.opened",
        vp.next,
        {"branch": rel, "number": info.number, "url": info.url, "base": prod, "forge": forge.name},
    )
    out.ok, out.url = True, info.url
    out.steps.append(f"opened {info.url} into {prod}; `pr sync` tags it once merged")
    return out


def _tag_and_push(
    repo: Path, cfg: Config, log: EventLog, out: Cut, vp: VersionPlan, *, branch: str, push: bool
) -> Cut:
    t = W.tag(repo, out.tag, out.sha, vp.notes)
    if not t.ok:
        out.refused, out.reason = True, f"could not tag {out.sha[:12]} as {out.tag}: {t.err}"
        return out
    out.steps.append(f"tagged {out.sha[:12]} as {out.tag}")
    if push:
        p = W.git(repo, "push", cfg.flow.remote, f"refs/tags/{out.tag}")
        if p.ok:
            out.pushed = True
            out.steps.append(f"pushed {out.tag} to {cfg.flow.remote}")
        else:
            out.reason = f"tagged locally, but the push failed: {p.err}"
    log.append(
        "release.tagged",
        vp.next,
        {
            "version": vp.next,
            "tag": out.tag,
            "sha": out.sha,
            "branch": branch,
            "items": vp.items,
            "pushed": out.pushed,
            "line": vp.line,
        },
    )
    out.ok = True
    return out


def _sync_releases(
    repo: Path, cfg: Config, log: EventLog, forge: FG.Forge, rep: SyncReport
) -> None:
    """Tag a gitflow release request once a person merged it, and send it back to develop."""
    st = _state(log)
    for ver, pend in sorted(st.pending_releases.items()):
        rep.checked += 1
        try:
            info = forge.view(int(pend.get("number") or 0))
        except (FG.ForgeUnavailable, FG.ForgeError) as exc:
            rep.unavailable.append(f"release {ver}: {exc}")
            continue
        if info.state == "closed":
            # Recorded, so the version is free again and the request is not re-polled
            # forever. The release branch is left for a person: it may hold fixes.
            log.append("release.closed", ver, {"url": info.url, "branch": pend.get("branch", "")})
            rep.changes.append(
                Change(
                    f"release {ver}", "closed", "closed without merging; version freed", info.url
                )
            )
            continue
        if info.state != "merged":
            rep.waiting.append(
                {
                    "id": f"release {ver}",
                    "url": info.url,
                    "review": info.review,
                    "checks": info.checks,
                    "base": info.base,
                }
            )
            continue
        W.fetch(repo, cfg.flow.remote, info.base)
        sha = info.merge_sha or info.head_sha
        if not W.rev(repo, sha):
            rep.unavailable.append(
                f"release {ver}: merge commit {sha[:12]} is not in the local clone after a fetch"
            )
            continue
        vp = VersionPlan(ref=info.base, next=ver, notes=f"Release {ver}\n\n{info.url}\n")
        vp.items = [
            i.id
            for i in st.items.values()
            if i.state == DONE
            and reached(repo, cfg, i, sha)
            and i.id not in {x for r in st.releases for x in r.items}
        ]
        c = Cut(version=ver, tag=f"{cfg.flow.tag_prefix}{ver}", sha=sha)
        c = _tag_and_push(repo, cfg, log, c, vp, branch=info.base, push=True)
        if not c.ok:
            rep.refused.append(f"release {ver}: {c.reason}")
            continue
        rep.changes.append(
            Change(
                f"release {ver}",
                "tagged",
                f"{c.tag} at {sha[:12]}" + ("" if c.pushed else f" (NOT pushed: {c.reason})"),
                info.url,
            )
        )
        try:
            # FROM production, not from the release branch: the tag sits on the merge
            # commit production made, and only merging THAT makes the tag reachable from
            # develop. Back-merging the release branch leaves develop computing its next
            # version from the previous tag and counting this release's commits twice.
            back = forge.create(
                head=info.base,
                base=cfg.flow.develop_branch,
                title=f"Back-merge release {ver} into {cfg.flow.develop_branch}",
                body=f"Brings {c.tag} into {cfg.flow.develop_branch}, so the next version is computed from a tag develop contains.",
                draft=False,
                labels=list(cfg.flow.pr_labels),
                reviewers=list(cfg.flow.pr_reviewers),
            )
            rep.changes.append(
                Change(f"release {ver}", "back_merge", f"into {cfg.flow.develop_branch}", back.url)
            )
        except (FG.ForgeError, FG.ForgeUnavailable) as exc:
            rep.refused.append(f"release {ver}: back-merge request could not be opened: {exc}")
