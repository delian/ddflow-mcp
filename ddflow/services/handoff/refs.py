"""Where a suggestion's patch lives: a hidden ref cut on the owner's tip, and a blob.

The ref `refs/ddflow/suggest/<owner item>/<id>` is built with git plumbing only (a scratch
index, `apply --cached`, `write-tree`, `commit-tree`, `update-ref`): no checkout, no touch of
the owner's tree or index. The redacted patch text is also stored content-addressed under
`.ddflow/blobs` for a remote owner that has no access to the ref. Git is reached only through
`infra.git`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ...core import blobref as B
from ...infra import fsio, git
from ...infra.blobstore import BlobStore

NAMESPACE = "refs/ddflow/suggest"
CLEAN, CONFLICT, UNAVAILABLE = "clean", "conflict", "unavailable"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_IDENT = {
    "GIT_AUTHOR_NAME": "ddflow",
    "GIT_AUTHOR_EMAIL": "ddflow@localhost",
    "GIT_COMMITTER_NAME": "ddflow",
    "GIT_COMMITTER_EMAIL": "ddflow@localhost",
}


@dataclass(frozen=True)
class Cut:
    probe: str  #: clean | conflict | unavailable
    ref: str = ""
    why: str = ""


def _part(text: str, fallback: str) -> str:
    """One ref component git accepts: no `..`, no leading dot or dash, no `.lock` suffix."""
    out = _SAFE.sub("-", text)
    while ".." in out:
        out = out.replace("..", ".")
    out = out.strip(".-")
    while out.endswith(".lock"):
        out = out.removesuffix(".lock").strip(".-")
    return out or fallback


def ref_name(owner: str, sid: str) -> str:
    """`refs/ddflow/suggest/OWNER/ID`, each part made a safe ref component. Every part carries a short digest of its original text, so two different
    inputs never share a ref (the event keeps the ref name; nothing derives it again)."""

    def one(text: str, fallback: str) -> str:
        part = _part(text, fallback)
        return f"{part}-{B.address(text.encode())[7:23]}"

    return f"{NAMESPACE}/{one(owner, 'owner')}/{one(sid, 'id')}"


def store_blob(root: Path | str, patch: str) -> str:
    """Store the (already redacted) patch; its address is what the event keeps."""
    raw = patch.encode()
    ref = B.address(raw)
    BlobStore(root).put(ref, raw)
    return ref


def read_blob(root: Path | str, ref: str) -> str | None:
    raw = BlobStore(root).get_bytes(ref)
    return None if raw is None else raw.decode("utf-8", "replace")


def patch_id(repo: Path | str, patch: str) -> str:
    """git's stable patch-id of ``patch`` ("" when git could not say)."""
    r = git.run(repo, "patch-id", "--stable", input=patch)
    return r.out.split()[0] if r.ok and r.out.split() else ""


def cut(repo: Path | str, tip: str, patch: str, ref: str, message: str) -> Cut:
    """Apply ``patch`` to ``tip`` in a scratch index and point ``ref`` at the result."""
    if not tip:
        return Cut(UNAVAILABLE, why="no base commit was given, so no ref was cut")
    with fsio.scratch_dir("ddflow-sg-") as tmp:
        env = {**_IDENT, "GIT_INDEX_FILE": str(tmp / "index")}
        steps = (
            ("read-tree", tip),
            ("apply", "--cached", "--whitespace=nowarn", "-"),
        )
        for args in steps:
            r = git.run(repo, *args, env=env, input=patch if args[0] == "apply" else None)
            if r.unavailable:
                return Cut(UNAVAILABLE, why=r.err)
            if not r.ok:
                return Cut(CONFLICT if args[0] == "apply" else UNAVAILABLE, why=r.err[:300])
        tree = git.run(repo, "write-tree", env=env)
        commit = git.run(repo, "commit-tree", tree.out, "-p", tip, "-m", message, env=env)
        if not (tree.ok and commit.ok):
            return Cut(UNAVAILABLE, why=(tree.err or commit.err)[:300])
        upd = git.run(repo, "update-ref", ref, commit.out)
        if not upd.ok:
            return Cut(UNAVAILABLE, why=upd.err[:300])
    return Cut(CLEAN, ref=ref)
