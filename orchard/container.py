"""Container awareness — the handful of things that differ inside one.

Orchard runs the same code in a container as outside it, but four assumptions that are
safe on a host are wrong in a container, and each fails quietly:

1. **Worktrees default to a SIBLING of the repo** (`../.orchard-worktrees`), which is
   deliberate on a host: it keeps sibling worktrees out of the agent's own file globs
   and test collection. Inside a container only the repo is bind-mounted, so a sibling
   path lands on the ephemeral layer and is destroyed when the container exits — taking
   an agent's uncommitted work with it. This is the one that loses data.
2. **`127.0.0.1` means the container**, not the host, so a locally-served model that
   `orchard reviewers detect` found on the host is unreachable from inside.
3. **Absolute paths are container paths.** Handled at the source — worktree paths are
   stored relative to the repo root — but a config written on a host and read in a
   container can still carry one.
4. **git has no identity and refuses the mount as "dubious ownership"**, handled by the
   entrypoint before the server starts.

Detection is conservative: a false positive would relocate worktrees on a host, so it
requires positive evidence rather than absence of evidence.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import proc as P

HOST_ALIAS = "host.docker.internal"


def in_container() -> bool:
    """Are we inside a container? Positive evidence only.

    `ORCHARD_IN_CONTAINER` is set by our own image and is the authoritative signal;
    the rest are the standard fallbacks for an image someone else built.
    """
    if os.environ.get("ORCHARD_IN_CONTAINER") == "1":
        return True
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text("utf-8", errors="replace")
    except OSError:
        return False
    return any(m in cgroup for m in ("docker", "containerd", "kubepods", "libpod"))


def default_worktree_root(configured: str) -> str:
    """Relocate a sibling worktree root to inside the repo when containerised.

    Only a path that escapes the repository is rewritten; an operator who set an
    explicit inside-the-repo root keeps it, and one who set an absolute path is assumed
    to have mounted it deliberately and is left alone (with a `doctor` note).
    """
    if not in_container():
        return configured
    if os.path.isabs(configured):
        return configured
    if not configured.startswith(".."):
        return configured
    return ".orchard-worktrees"


def rewrite_localhost(url: str) -> str:
    """Point a loopback endpoint at the host.

    Inside a container `127.0.0.1` is the container's own loopback, so a reviewer
    endpoint discovered on the host is simply not there. Docker Desktop resolves
    `host.docker.internal` automatically; on Linux the operator must add
    `--add-host=host.docker.internal:host-gateway`, which `warnings()` says.
    """
    if not in_container():
        return url
    for needle in ("127.0.0.1", "localhost", "[::1]"):
        if needle in url:
            return url.replace(needle, HOST_ALIAS)
    return url


def warnings(repo: Path, cfg) -> list[str]:
    """Container-specific problems worth telling the operator about."""
    if not in_container():
        return []
    out: list[str] = []
    root = cfg.worktree.root
    if os.path.isabs(root):
        out.append(
            f"worktree.root is the absolute path {root!r}. Inside a container that is "
            f"only durable if you mounted it; otherwise every worktree is lost when the "
            f"container exits. Prefer a path inside the repository."
        )
    elif root.startswith(".."):
        out.append(
            f"worktree.root {root!r} resolves OUTSIDE the mounted repository, so "
            f"worktrees would be written to the container's ephemeral layer and "
            f"destroyed on exit. Orchard is relocating them to '.orchard-worktrees' "
            f"inside the repo; set it explicitly to silence this."
        )
    for name, url in _reviewer_urls(repo):
        if any(n in url for n in ("127.0.0.1", "localhost", "[::1]")):
            out.append(
                f"reviewer {name!r} points at {url}, which inside a container is the "
                f"CONTAINER's loopback, not your machine. Orchard rewrites it to "
                f"{HOST_ALIAS}; on Linux you must also run the container with "
                f"--add-host={HOST_ALIAS}:host-gateway or it will not resolve."
            )
    if not os.environ.get("GIT_AUTHOR_EMAIL") and not _repo_has_identity(repo):
        out.append(
            "no git identity is configured. Commits made in this container will be "
            "attributed to a placeholder. Pass -e GIT_AUTHOR_NAME -e GIT_AUTHOR_EMAIL, "
            "or set user.email in the repository."
        )
    return out


def _reviewer_urls(repo: Path) -> list[tuple[str, str]]:
    try:
        from .reviewer import load_reviewers

        return [(r.name, r.base_url) for r in load_reviewers(repo) if r.enabled]
    except Exception:
        return []


def _repo_has_identity(repo: Path) -> bool:
    r = P.run(
        ["git", "-C", str(repo), "config", "user.email"], capture_output=True, text=True, timeout=30
    )
    return bool(r.stdout.strip())
