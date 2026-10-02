"""``ddflow version cut --changelog``: the new version's section, written through export.

The changelog kind (``services/export/kind_changelog.py``) places every entry by the version
tags that contain it. At cut time the tag does not exist yet, and the file written by the cut
must be in the commit the tag will name. So the section is rendered with the tag present for a
moment: a temporary annotated tag at the release source, removed again before this returns,
whatever happens. The rendering is then exactly what ``ddflow export changelog`` prints once
the real tag exists: the Unreleased entries become ``## [x.y.z] - date`` with its compare
link, and an empty Unreleased remains.

* mode ``whole`` (the default): the framed document replaces the file; a file that is not
  ddflow's, or was edited since, is refused without ``force`` (``write.write_whole``).
* mode ``region``: the hand-kept file's ``changelog`` region is rewritten to the empty
  Unreleased section and the new version section follows it, with the ``[Unreleased]`` link
  line updated and the version's link added when the file keeps link lines.
* mode ``append``: refused; its entries are not versioned sections.

The file is committed on the branch the tag will name, never in the working tree of a branch
somebody else has checked out. Failures reading the log or git are ``ExportError`` exit 2; a
file ddflow will not overwrite is exit 3.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..infra import tomlcfg
from ..infra import worktree as W
from .export import frame as F
from .export import kind_changelog as KC
from .export import ops
from .export import registry as R
from .export import write as EW
from .export.query import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError

DOC = "changelog"


@dataclass
class Prepared:
    path: str
    mode: str
    notes: str  # the version's section, for the tag message and the release request
    apply: Callable[..., str]  # (tree, *, force, dry) -> created | updated | unchanged | diff


def _git(repo: Path, *args: str) -> W.GitResult:
    try:
        return W.git(repo, *args)
    except Exception as exc:  # a timeout or a missing git
        raise ExportError(
            f"could not run git: {type(exc).__name__}: {exc}", EXIT_UNAVAILABLE
        ) from exc


def prepare(repo: Path, cfg: Config, *, version: str, ref: str, fallback_notes: str) -> Prepared:
    """Render everything the cut will write, with the new tag temporarily present at ``ref``."""
    spec = ops.spec_for(cfg, DOC, writing=True)
    if spec.mode == R.APPEND:
        raise ExportError(
            f"--changelog needs a changelog in mode whole or region: [export.changelog] mode is "
            f"{R.APPEND!r}, which appends entries and has no version sections",
            EXIT_REFUSED,
        )
    tag = f"{cfg.flow.tag_prefix}{version}"
    sha = W.rev(repo, ref)
    if not sha:
        raise ExportError(f"release source {ref!r} does not exist", EXIT_UNAVAILABLE)
    made = _git(
        repo,
        "tag",
        "--annotate",
        tag,
        "-m",
        "ddflow: temporary, while the changelog is rendered",
        sha,
    )
    if not made.ok:
        raise ExportError(
            f"could not tag {sha[:12]} to render the changelog: {made.err}", EXIT_UNAVAILABLE
        )
    try:
        q = ops.load(repo, cfg)
        flt = dataclasses.replace(spec.filters, tag="")
        slice_ = dataclasses.replace(spec.filters, tag=version)
        notes = ops._body(repo, cfg, q, dataclasses.replace(spec, filters=slice_))
        if spec.mode == R.WHOLE:
            doc = ops.render(repo, cfg, q, dataclasses.replace(spec, filters=flt), max_bytes=0)
            apply = _whole(spec.path, doc)
        else:
            unreleased = ops._body(
                repo,
                cfg,
                q,
                dataclasses.replace(spec, filters=dataclasses.replace(flt, tag=KC.UNRELEASED)),
            )
            links = {x["label"]: x["url"] for x in KC.build_sections(q)["links"]}
            apply = _region(spec.path, unreleased, notes, version, links)
    finally:
        _git(repo, "tag", "-d", tag)
    has_entries = "\n### " in notes
    return Prepared(spec.path, spec.mode, notes if has_entries else fallback_notes, apply)


def _whole(path: str, doc: str) -> Callable[..., str]:
    def apply(tree: Path, *, force: bool, dry: bool) -> str:
        return EW.write_whole(tree, path, doc, force=force, diff=dry).action

    return apply


def _region(
    path: str, unreleased: str, section: str, version: str, links: dict[str, str]
) -> Callable[..., str]:
    def apply(tree: Path, *, force: bool, dry: bool) -> str:
        target, rel = EW.safe_target(tree, path)
        with tomlcfg.locked(EW._lock_path(tree)):
            old = EW._read(target)
            if old is None:
                raise EW.Refused(
                    f"{rel} does not exist: --changelog in mode region updates a hand-kept file "
                    f"that has the ddflow region markers"
                )
            begins = list(EW._region_pattern(DOC, "begin").finditer(old))
            ends = list(EW._region_pattern(DOC, "end").finditer(old))
            if len(begins) != 1 or len(ends) != 1 or begins[0].end() > ends[0].start():
                raise EW.Refused(
                    f"{rel} needs exactly one ddflow region for {DOC!r} "
                    f"({EW._begin(DOC, '<digest>')} ... {EW._end(DOC)}); "
                    f"--force does not repair or add markers"
                )
            b, e = begins[0], ends[0]
            current = old[b.end() + 1 : e.start()]
            if F.body_digest(current) != b.group("sha") and not force:
                raise EW.Refused(
                    f"the {DOC!r} region of {rel} was edited by hand since ddflow wrote it; "
                    f"refusing to overwrite (--force to replace)"
                )
            rest = old[e.end() + 1 :]
            new_section = F.normalize(section)
            if rest and not rest.startswith(("\n", "\r\n")):
                new_section += "\n"
            new = old[: b.start()] + EW.region_text(DOC, unreleased) + "\n" + new_section + rest
            new = _relink(new, version, links)
            if dry:
                return "diff"
            if new == old:
                return "unchanged"
            EW._commit(target, new)
            return "updated"

    return apply


_UNRELEASED_LINK = re.compile(r"^\[Unreleased\]:[ \t]*\S.*$", re.M)


def _relink(text: str, version: str, links: dict[str, str]) -> str:
    """Point ``[Unreleased]:`` at the new tag and add ``[version]:`` below it, when the file
    keeps Keep a Changelog link lines. A file without them is left as it is."""
    m = _UNRELEASED_LINK.search(text)
    if not m or "Unreleased" not in links or version not in links:
        return text
    lines = f"[Unreleased]: {links['Unreleased']}\n[{version}]: {links[version]}"
    return text[: m.start()] + lines + text[m.end() :]


def _tree_for(repo: Path, cfg: Config, branch: str):
    """``(path, throwaway)``: where ``branch`` can be committed to without touching anyone."""
    root = W.repo_root(repo)
    if not _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").ok:
        raise ExportError(f"branch {branch!r} does not exist locally", EXIT_UNAVAILABLE)
    where = W.checked_out_at(root, branch)
    if where is not None and where.resolve() != root.resolve():
        raise ExportError(
            f"{branch!r} is checked out in the worktree {where}: ddflow will not write the "
            f"changelog into a tree someone may be working in",
            EXIT_REFUSED,
        )
    return root, where is None


def commit_on(
    repo: Path, cfg: Config, branch: str, prep: Prepared, *, force: bool, message: str
) -> str:
    """Write and commit the prepared changelog on local ``branch``. Returns the action."""
    import tempfile

    root, throwaway = _tree_for(repo, cfg, branch)
    tmp: Path | None = None
    tree = root
    if throwaway:
        base = (root / cfg.worktree.root).resolve()
        base.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f".changelog-{W.safe_name(branch)}-", dir=base))
        tmp.rmdir()
        add = _git(root, "worktree", "add", str(tmp), branch)
        if not add.ok:
            raise ExportError(
                f"could not stage {branch} for the changelog: {add.err}", EXIT_UNAVAILABLE
            )
        tree = tmp
    try:
        dirty = _git(tree, "status", "--porcelain", "--", prep.path)
        if not dirty.ok:
            raise ExportError(f"git status failed: {dirty.err}", EXIT_UNAVAILABLE)
        if dirty.out.strip() and not force:
            raise EW.Refused(
                f"{prep.path} has uncommitted changes; refusing to commit them into the "
                f"release (commit or discard them, or --force)"
            )
        action = prep.apply(tree, force=force, dry=False)
        if action in ("created", "updated"):
            for step in (("add", "--", prep.path), ("commit", "-m", message, "--", prep.path)):
                r = _git(tree, *step)
                if not r.ok:
                    raise ExportError(
                        f"git {step[0]} of {prep.path} failed: {r.err or r.out}", EXIT_UNAVAILABLE
                    )
        return action
    finally:
        if tmp is not None:
            _git(root, "worktree", "remove", "--force", str(tmp))
            _git(root, "worktree", "prune")


__all__ = ["Prepared", "commit_on", "prepare"]
