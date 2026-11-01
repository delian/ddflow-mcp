"""Agent identity (``default_agent_id``) and this clone's identity suffix."""

from __future__ import annotations

import contextlib
import getpass
import glob
import json
import os
import re
import secrets
from pathlib import Path

from .. import fsio
from .. import git as G
from .. import hostinfo as H
from .. import paths as P
from .. import worktree as W

_AGENT_ID_CACHE: dict[str, str] = {}


def _toplevel(path: str) -> str:
    r = G.run(path, "rev-parse", "--show-toplevel", timeout=G.PROBE_TIMEOUT)
    return r.out if r.ok else ""


def _common_dir(path: str) -> str:
    found = P.common_dir(path)
    return str(found) if found else ""


def default_agent_id(fallback_root: Path | str | None = None) -> str:
    """A stable identity for "the agent working here".

    It used to be ``{host}-{pid}``, which is stable for exactly one process — so
    `ddflow claim` and the `git commit` hook that ran seconds later were different
    agents. The hook then refused the holder's own commit **and told them their lease
    belonged to somebody else**. Out of the box, the enforcement layer rejected correct
    behaviour and blamed the user for it.

    The working model is **one agent per worktree**, so the worktree is the identity:

    * inside a worktree of the managed repository -> that worktree's name, so two
      parallel agents differ while every process inside one worktree agrees;
    * anywhere else -> the managed repository's own name, so a command run with
      ``--repo X`` from an unrelated directory still lands on X's identity rather than
      on whatever happened to be the shell's cwd.

    ``DDFLOW_AGENT`` overrides both, and a harness running several agents inside ONE
    tree must set it — there is no signal that can distinguish them otherwise.

    Host and tree name are not unique ACROSS CLONES: two machines both called `ubuntu`
    with the repository checked out as `ddflow` both derived `ubuntu-ddflow`, wrote one
    shard between them, and `merge=union` then interleaved two writers' clocks in it
    (B190). So once the project is adopted the id also carries this clone's random
    suffix (:func:`_clone_suffix`) — shared by every worktree of the clone, because the
    suffix lives in the primary checkout, so the claim and the commit hook still agree.
    """
    root = str(fallback_root or "")
    key = f"{os.getcwd()}|{root}"
    if key in _AGENT_ID_CACHE:
        return _AGENT_ID_CACHE[key]
    ident = bare_agent_id(fallback_root)
    suffix = _clone_suffix(Path(root)) if root else ""
    if not suffix:
        # Not cached: an unadopted repo gains its suffix at `ddflow init`, and a
        # long-lived MCP server must pick that up rather than keep the bare name.
        return ident
    ident = f"{ident}-{suffix}"
    _AGENT_ID_CACHE[key] = ident
    return ident


def bare_agent_id(fallback_root: Path | str | None = None) -> str:
    """The derived id WITHOUT this clone's suffix: `{host}-{tree}`, which is what every
    derived id was before B190, and so the holder of any lease claimed before it."""
    host = H.short_host()
    root = str(fallback_root or "")
    name = ""
    here = _toplevel(os.getcwd())
    # Only trust the cwd when it belongs to the SAME repository we are managing;
    # otherwise `ddflow --repo /elsewhere` run from another checkout would take that
    # checkout's identity, and the hook in /elsewhere would disagree with it.
    if here and (not root or _common_dir(here) == _common_dir(root)):
        name = Path(here).name
    elif root:
        name = Path(root).name
    if not name:
        try:
            name = getpass.getuser()
        except Exception:
            name = "agent"
    return f"{host}-{name}"


def tree_agent_ids(tree: Path | str, root: Path | str) -> set[str]:
    """The identities an agent standing in the linked worktree ``tree`` of the clone at
    ``root`` derives for itself: `{host}-{tree name}`, bare and with this clone's suffix.

    What :func:`default_agent_id` answers from INSIDE ``tree``, asked from anywhere --
    so a command can tell whether the tree it was run from is some other identity's
    working tree. Never creates the suffix: an unadopted project has none to compare.
    """
    bare = f"{H.short_host()}-{Path(tree).name}"
    path = Path(root) / CLONE_ID_FILE
    try:
        suffix = path.read_text("utf-8").strip()
    except OSError:
        suffix = ""
    return {bare, f"{bare}-{suffix}"} if suffix else {bare}


def clone_suffix_since(root: Path | str) -> float:
    """When this clone got its suffix (the clone-id file's mtime; it is never rewritten),
    or 0.0 when it has none. Before that moment this clone derived the bare id.

    "Has a suffix" means what :func:`_clone_suffix` means by it -- the file has content --
    so an empty file is no suffix here either."""
    path = Path(root) / CLONE_ID_FILE
    try:
        return path.stat().st_mtime if path.read_text("utf-8").strip() else 0.0
    except OSError:
        return 0.0


#: Where a clone keeps its identity suffix. Under `.ddflow/local/`, which is gitignored:
#: a committed suffix would be every clone's suffix, which is the collision again.
CLONE_ID_FILE = Path(".ddflow") / "local" / "clone-id"


_SUFFIX = re.compile(r"[0-9a-f]{6}")
_TREE_KINDS = frozenset({"lease.acquired", "lease.renewed", "worktree.created", "worktree.adopted"})
_NO_RANK: tuple[float, int] = (-1.0, -1)


def _shard_evidence(
    shard: Path, bare: str
) -> tuple[dict[str, tuple[tuple[float, int], str]], dict[str, set[str]]]:
    """From one `{bare}-*.jsonl` shard: item -> (newest claim rank, suffix) of the claims
    held by `{bare}-{suffix}`, and item -> the worktree paths the shard records for it."""
    claim: dict[str, tuple[tuple[float, int], str]] = {}
    trees: dict[str, set[str]] = {}
    try:
        lines = shard.read_text("utf-8").splitlines()
    except OSError:
        return claim, trees
    for ln in lines:
        if '"lease.' not in ln and '"worktree.' not in ln:
            continue
        try:
            ev = json.loads(ln)
            kind, item, data = ev["kind"], ev["subject"], ev["data"]
            holder = str(data.get("holder") or "")
            suffix = holder[len(bare) + 1 :]
            if (
                kind == "lease.acquired"
                and holder.startswith(bare + "-")
                and _SUFFIX.fullmatch(suffix)
            ):
                rank = (float(data.get("at") or 0.0), int(ev.get("lamport") or 0))
                if rank > claim.get(item, (_NO_RANK, ""))[0]:
                    claim[item] = (rank, suffix)
            stored = str(data.get("worktree") or data.get("path") or "")
            if stored and kind in _TREE_KINDS:
                trees.setdefault(item, set()).add(stored)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return claim, trees


def _recovered_suffix(root: Path) -> str:
    """The suffix this clone had before its git-ignored `clone-id` was deleted, or "".

    `rm -r .ddflow/local` (git clean -xfd, a re-image) used to make the user a NEW agent,
    locked out of their own claims (B839f599cb5). The log still holds the evidence: a
    `lease.acquired` whose holder is `{bare id}-{suffix}` and whose recorded worktree is a
    linked worktree of THIS clone, on disk and in its `git worktree list`. Several
    suffixes: the newest claim wins. A fresh clone of the project, or a teammate's shard,
    has no such worktree here, so it mints as before. Read only; shards are never renamed.
    """
    top = root.resolve()
    bares = {bare_agent_id(root), f"{H.short_host()}-{root.name}"}
    best, found = _NO_RANK, ""
    for bare in bares:
        for shard in (root / ".ddflow" / "events").glob(f"{glob.escape(bare)}-*.jsonl"):
            claim, trees = _shard_evidence(shard, bare)
            for item, (rank, suffix) in claim.items():
                if rank > best and any(_linked_tree_here(top, t) for t in trees.get(item, ())):
                    best, found = rank, suffix
    return found


def _linked_tree_here(top: Path, stored: str) -> bool:
    """Is `stored` a LINKED worktree of the clone at `top`: a directory other than the
    checkout itself (a `[worktree] root` may lie beside it) that this clone's own
    `git worktree list` names?

    A stored path is relative, so another clone records the very same one. The checkout's
    own root is no evidence (every clone has one at the same relative path), and a copied
    directory still lists the original's path; only a linked tree here is."""
    try:
        tree = W.load_path(top, stored).resolve()
        if tree == top or not tree.is_dir():
            return False
        return any(
            Path(e["worktree"]).resolve() == tree for e in W.list_worktrees(top) if "worktree" in e
        )
    except OSError:
        return False


def _clone_suffix(root: Path) -> str:
    """This clone's random identity suffix, created on first use and never changed.

    `root` is the PRIMARY checkout (`worktree.repo_root`), so every worktree of one
    clone reads the same file. "" when the project is not adopted — deriving an id must
    not create `.ddflow/` in someone's repository, or the "is this adopted?" check
    answers yes about itself — and "" when the file cannot be written.

    Created by hard-linking a fully written temp file into place, so two processes
    racing to create it agree on ONE value and neither can read a half-written one.
    The directory gets its own `.gitignore`: a project whose `.ddflow/.gitignore`
    predates `local/` must not commit the suffix and hand it to every clone.
    """
    path = root / CLONE_ID_FILE
    if not path.parent.parent.is_dir():
        return ""
    with contextlib.suppress(FileNotFoundError):
        return path.read_text("utf-8").strip()
    try:
        fsio.ensure_ignored_dir(path.parent)
        # Exclusive: the first writer wins and the rest read its value. Where there are no
        # hard links (some FUSE and SMB mounts) a reader racing it may see it empty for an
        # instant, and an empty read is not cached, so that caller simply asks again.
        with contextlib.suppress(FileExistsError):
            fsio.atomic_write(
                path, (_recovered_suffix(root) or secrets.token_hex(3)) + "\n", exclusive=True
            )
        return path.read_text("utf-8").strip()
    except OSError:
        return ""
