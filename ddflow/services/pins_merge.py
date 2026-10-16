"""The git side of pin regeneration: a merge driver and a post-merge hook (DX-pins-regenerate).

Two branches that each re-record the same pin file conflict in it. The driver merges the prose
of a mixed file normally and keeps our side of the generated regions, so the merge does not stop
over them (the tree is not complete while it runs, so it cannot regenerate); a prose conflict
both sides made stays a conflict. The post-merge hook, which runs once the tree is final,
regenerates the stale pins and folds them into the merge commit.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..infra import git as G
from ..infra.fsio import atomic_write, replace_text
from .enforce import command_line, hooks_dir
from .pins import pin_files, regenerate
from .pins_text import merge_into

#: The merge driver's name in `.gitattributes` and `git config merge.<name>.driver`.
DRIVER = "pinsregen"
HOOK_MARK = "# DDFLOW-PINS-HOOK v1 -- managed by `ddflow pins regenerate --install`"


def merge_driver(paths: str | list[str]) -> int:
    """`git merge` driver for a pin file (the file at `ours` is the result): the prose of the
    three versions merged, our side of the generated regions kept; the post-merge hook then
    re-records every stale pin. ``paths`` is what git passes (`%A %O %B`: ours, the common
    ancestor, theirs); a lone path keeps ours. 1 (a conflict) only for prose both sides edited."""
    given = [paths] if isinstance(paths, str) else list(paths)
    ours, base, theirs = [*given, "", "", ""][:3]
    if not Path(ours or "-").exists():
        return 1
    if not (base and theirs and Path(base).exists() and Path(theirs).exists()):
        return 0
    return 1 if merge_into(Path(ours), Path(base), Path(theirs)) else 0


def _fold_into_merge(repo: Path, files: list[str], done: list[str]) -> str:
    """Write the merge commit again with the regenerated pin files in it.

    `git commit --amend` is refused while MERGE_HEAD still exists, which it does in the
    post-merge hook: the commit is written with plumbing and the branch moved to it."""
    added = G.run(repo, "add", "-A", "--", *files)
    tree = G.run(repo, "write-tree")
    head = G.run(repo, "rev-parse", "HEAD")
    parents = G.run(repo, "rev-list", "--parents", "-n", "1", "HEAD").out.split()[1:]
    msg = G.run(repo, "log", "-1", "--format=%B", "HEAD")
    steps = (added, tree, head, msg)
    bad = next((r for r in steps if not r.ok), None)
    # `commit-tree` ignores commit.gpgsign, which `git merge` honoured for the commit it replaces.
    # The commit being replaced decides (`git merge -S` signs without any config); the config
    # is the fallback for a merge whose signature cannot be read back.
    was = G.run(repo, "log", "-1", "--format=%G?", "HEAD").out.strip()
    cfgd = G.run(repo, "config", "--type=bool", "--get", "commit.gpgsign").out.strip() == "true"
    signed = (was not in ("", "N")) if was else cfgd
    new = bad or G.run(
        repo,
        "commit-tree",
        *(["-S"] if signed else []),
        tree.out.strip(),
        *[x for p in parents for x in ("-p", p)],
        input=msg.out + "\n",
    )
    if new.ok and G.run(repo, "update-ref", "HEAD", new.out.strip(), head.out.strip()).ok:
        return f"pins regenerated into the merge commit: {', '.join(done)}"
    return f"pins regenerated, not committed ({new.err}): {', '.join(done)}"


def after_merge(repo: Path, cfg: Config) -> str:
    """Post-merge: regenerate; fold the result into the merge commit when only pin files
    changed. Returns one line saying what happened."""
    mode = cfg.pins.after_merge
    if mode == "off":
        return ""
    rep = regenerate(repo, cfg)
    if rep.problem or not rep.pins:
        return ""
    if rep.failed:
        return "pins: " + "; ".join(f"{p.name}: {p.error}" for p in rep.failed)
    done = [p.name for p in rep.pins if p.regenerated]
    if not done:
        return ""
    files = pin_files(repo, cfg)
    paths = G.run(repo, "diff", "--name-only", "-z", "HEAD", z=True).paths() or []
    own = tuple(f.rstrip("/") for f in files)
    merge = G.run(repo, "rev-parse", "-q", "--verify", "HEAD^2").ok
    if mode == "amend" and merge and paths and all(p.startswith(own) for p in paths):
        return _fold_into_merge(repo, files, done)
    return f"pins regenerated, not committed: {', '.join(done)}; commit them"


def install(repo: Path, cfg: Config, *, line=None) -> list[str]:
    """Write the merge driver for the recipes' files, and the post-merge hook; the actions."""
    line = line or command_line
    files = pin_files(repo, cfg)
    # Keeping our side is only safe while the post-merge hook re-records the other side's
    # pins: with after_merge off nothing would, so nothing is installed.
    if not files or cfg.pins.after_merge == "off":
        return []
    actions: list[str] = []
    attrs = Path(repo) / ".gitattributes"
    text = attrs.read_text("utf-8") if attrs.exists() else ""
    have = set(text.splitlines())
    add = [
        f"{f.rstrip('/')}{'/**' if f.endswith('/') or (Path(repo) / f).is_dir() else ''} merge={DRIVER}"
        for f in files
    ]
    new = [a for a in add if a not in have]
    if new:
        body = text + ("" if not text or text.endswith("\n") else "\n") + "\n".join(new) + "\n"
        replace_text(attrs, body)
        actions += [f"added '{a}' to .gitattributes" for a in new]
    driver = line("pins merge-driver", extra="%A %O %B")
    for key, val in (
        (f"merge.{DRIVER}.name", "ddflow pin file: merge the prose, regenerate after the merge"),
        (f"merge.{DRIVER}.driver", driver),
    ):
        cur = G.run(repo, "config", "--local", "--get", key)
        if cur.out.strip() != val:
            G.run(repo, "config", "--local", key, val)
            actions.append(f"set git config {key}")
    hook = hooks_dir(repo) / "post-merge"
    body = f"#!/bin/sh\n{HOOK_MARK}\n{line('pins regenerate --after-merge')}\n"
    if not hook.exists() or HOOK_MARK in hook.read_text("utf-8", errors="replace"):
        if not hook.exists() or hook.read_text("utf-8") != body:
            hook.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(hook, body)
            hook.chmod(0o755)
            actions.append(f"wrote {hook}")
    else:
        actions.append(f"{hook} is not ddflow's; add `ddflow pins regenerate --after-merge` to it")

    return actions
