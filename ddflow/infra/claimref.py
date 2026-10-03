"""Opt-in cross-machine claim lock: a claim also creates `refs/ddflow/claims/<id>` on the remote.

Git cannot stop two offline clones claiming one item (B191 only detects it afterwards). A
server-side ref create is compare-and-swap, so while online it is real mutual exclusion
with no server of our own. Each claim pushes a UNIQUE empty-tree commit (holder, expiry
and a nonce in the message) to the ref: a second claimant's different commit is not a
fast-forward, so the remote rejects it. The expiry rides in the commit, so a crashed
holder's ref is replaced by a compare-and-swap (`--force-with-lease`) once it lapses.

Every function returns a `Result`; `unavailable` (no remote, offline, timeout) is never
treated as success -- the caller refuses the claim rather than silently going local.
"""

from __future__ import annotations

import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .worktree import git

PREFIX = "refs/ddflow/claims/"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # built into git; needs no object
TIMEOUT_S = 30
_SAFE = re.compile(r"[^A-Za-z0-9_-]")  # a character that must be escaped
_IDENT = {
    "GIT_AUTHOR_NAME": "ddflow",
    "GIT_AUTHOR_EMAIL": "ddflow@localhost",
    "GIT_COMMITTER_NAME": "ddflow",
    "GIT_COMMITTER_EMAIL": "ddflow@localhost",
}


@dataclass(frozen=True)
class Result:
    status: str  # ok | held | unavailable
    holder: str = ""
    expires: float = 0.0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def ref_name(item: str) -> str:
    """A valid ref for any item id: every UTF-8 byte outside [A-Za-z0-9_-] becomes `%XX`.

    Byte-wise and fixed-width, so the mapping is injective and ids stay distinct."""
    return PREFIX + "".join(
        c
        if _SAFE.fullmatch(c) is None
        else "".join(f"%{b:02x}" for b in c.encode("utf-8", "surrogatepass"))
        for c in item
    )


def _run(root: Path, *args: str, env: dict[str, str] | None = None):
    try:
        return git(root, *args, timeout=TIMEOUT_S, env=env)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None if isinstance(exc, subprocess.TimeoutExpired) else exc


def _make_commit(root: Path, item: str, holder: str, expires: float, parent: str = "") -> str:
    tree = EMPTY_TREE
    msg = f"claim {item}\nholder: {holder}\nexpires: {expires:.0f}\nnonce: {uuid.uuid4().hex}\n"
    args = ["commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
    return git(root, *args, timeout=TIMEOUT_S, check=True, env=_IDENT).out


def _parse(message: str) -> tuple[str, float]:
    holder = re.search(r"^holder: (.*)$", message, re.M)
    exp = re.search(r"^expires: (\d+)$", message, re.M)
    return (holder.group(1) if holder else "", float(exp.group(1)) if exp else 0.0)


def _remote_sha(root: Path, remote: str, ref: str) -> tuple[str | None, str]:
    """(sha or "" when absent, error text); sha None means the remote could not be asked."""
    r = _run(root, "ls-remote", remote, ref)
    if r is None or isinstance(r, OSError) or not r.ok:
        return None, "remote unreachable" if r is None else (getattr(r, "err", "") or str(r))
    return (r.out.split()[0] if r.out else ""), ""


def _read(root: Path, remote: str, ref: str, sha: str) -> tuple[str, float] | None:
    """(holder, expires) of the claim at `sha`; None when it could not be read -- which is
    NOT the same as lapsed, and a caller must not treat it as one."""
    got = _run(root, "fetch", "--no-tags", remote, ref)
    if got is None or isinstance(got, OSError) or not got.ok:
        return None
    body = git(root, "cat-file", "commit", sha, timeout=TIMEOUT_S)
    return _parse(body.out) if body.ok else None


def take(root: Path, remote: str, item: str, holder: str, expires: float) -> Result:
    """Create the claim ref, or replace a lapsed one by compare-and-swap."""
    ref = ref_name(item)
    try:
        sha = _make_commit(root, item, holder, expires)
    except Exception as exc:
        return Result("unavailable", detail=f"could not build the claim commit: {exc}")
    pushed = _run(root, "push", "--quiet", remote, f"{sha}:{ref}")
    if pushed is not None and not isinstance(pushed, OSError) and pushed.ok:
        return Result("ok", holder, expires)
    cur, why = _remote_sha(root, remote, ref)
    if cur is None:
        return Result("unavailable", detail=why)
    if not cur:  # the ref is not there, so the push failed for another reason
        err = getattr(pushed, "err", "") or "push failed"
        return Result("unavailable", detail=err)
    read = _read(root, remote, ref, cur)
    if read is None:
        return Result("unavailable", detail="the existing claim could not be read")
    owner, lapses = read
    if owner == holder or lapses <= time.time():
        renewing = owner == holder
        swapped = _run(
            root, "push", "--quiet", f"--force-with-lease={ref}:{cur}", remote, f"{sha}:{ref}"
        )
        if swapped is not None and not isinstance(swapped, OSError) and swapped.ok:
            return Result("ok", holder, expires, "renewed" if renewing else "")
        cur, why = _remote_sha(root, remote, ref)
        if cur is None:
            return Result("unavailable", detail=why)
        read = _read(root, remote, ref, cur) if cur else None
        if read is None:
            return Result("unavailable", detail="the existing claim could not be read")
        owner, lapses = read
    return Result("held", owner, lapses, "claimed on the remote")


def renew(root: Path, remote: str, item: str, holder: str, expires: float) -> Result:
    """Extend our own claim: a child commit with the new expiry (a fast-forward)."""
    ref = ref_name(item)
    cur, why = _remote_sha(root, remote, ref)
    if cur is None:
        return Result("unavailable", detail=why)
    if not cur:
        return take(root, remote, item, holder, expires)
    read = _read(root, remote, ref, cur)
    if read is None:
        return Result("unavailable", detail="the existing claim could not be read")
    owner = read[0]
    if owner != holder:
        return Result("held", owner, read[1], "the remote claim is another agent's")
    try:
        sha = _make_commit(root, item, holder, expires, parent=cur)
    except Exception as exc:
        return Result("unavailable", detail=f"could not build the claim commit: {exc}")
    pushed = _run(root, "push", "--quiet", remote, f"{sha}:{ref}")
    ok = pushed is not None and not isinstance(pushed, OSError) and pushed.ok
    return (
        Result("ok", holder, expires) if ok else Result("unavailable", detail="renew push failed")
    )


def drop(root: Path, remote: str, item: str, holder: str) -> Result:
    """Delete the claim ref IF `holder` still owns it (compare-and-swap on the sha read).

    A holder that stalled past its expiry and was replaced must not delete its
    successor's claim when it finally releases."""
    ref = ref_name(item)
    cur, why = _remote_sha(root, remote, ref)
    if cur is None:
        return Result("unavailable", detail=why)
    if not cur:
        return Result("ok")
    read = _read(root, remote, ref, cur)
    if read is None:
        return Result("unavailable", detail="the existing claim could not be read")
    if read[0] != holder:
        return Result("held", read[0], read[1], "the remote claim is another agent's; left alone")
    gone = _run(root, "push", "--quiet", f"--force-with-lease={ref}:{cur}", remote, f":{ref}")
    ok = gone is not None and not isinstance(gone, OSError) and gone.ok
    return Result("ok") if ok else Result("unavailable", detail="could not delete the claim ref")
