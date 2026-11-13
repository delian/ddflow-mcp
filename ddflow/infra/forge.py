"""Compatibility re-export: the forge family moved to `ddflow.adapters.forge` (+ `forge_github`, `forge_gitlab`).

The `Forge` port, its `GitHub`/`GitLab` adapters, `PRInfo`/`Thread` values and the
`ForgeUnavailable`/`ForgeError` outcomes now live at the bottom `ddflow.adapters` layer; this
shim re-exports them so the flow services keep importing `ddflow.infra.forge`. `detect` stays
here: it reads configuration and the worktree remote, which are above the adapters layer.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ddflow.adapters.forge import (  # noqa: F401  # re-exported for the flow services
    FEEDBACK_MAX,
    FORGES,  # the registry, for callers that name a forge
    GITHUB,
    GITLAB,
    THREAD_BODY_MAX,
    Forge,
    ForgeError,
    ForgeUnavailable,
    PRInfo,
    Thread,
    _check,
    _clip,
    _count,
    _json,
    _run,
)
from ddflow.adapters.forge_github import (  # noqa: F401  # re-exported (was public here)
    _GH_QUEUE_QUERY,
    _GH_REPLY_MUTATION,
    _GH_RESOLVE_MUTATION,
    _GH_THREADS_QUERY,
    GitHub,
    _gh_checks,
)
from ddflow.adapters.forge_gitlab import GitLab  # noqa: F401  # re-exported (was public here)
from ddflow.adapters.registry import UnknownName

from ..config import Config
from .worktree import git


def detect(repo: Path, cfg: Config) -> Forge:
    """The forge for this repository. Raises ForgeUnavailable when it cannot be told.

    'auto' reads the remote URL. A self-hosted forge on a domain naming neither is
    common, so the fallback is whichever ONE CLI is installed -- and when both or neither
    are, the answer is to ask the operator to set it, never to guess: opening a request
    on the wrong forge is not an error anything downstream would catch.
    """
    name = cfg.flow.forge
    if name == "auto":
        url = git(repo, "remote", "get-url", cfg.flow.remote)
        low = url.out.lower() if url.ok else ""
        if "github" in low:
            name = GITHUB
        elif "gitlab" in low:
            name = GITLAB
        else:
            have = [n for n, b in ((GITHUB, "gh"), (GITLAB, "glab")) if shutil.which(b)]
            if len(have) != 1:
                raise ForgeUnavailable(
                    f"cannot tell which forge hosts remote {cfg.flow.remote!r} "
                    f"({url.out or url.err or 'no URL'}). Set [flow].forge to github or gitlab."
                )
            name = have[0]
    try:
        forge_cls = FORGES.get_or_raise(name)
    except UnknownName:
        raise ForgeUnavailable(f"[flow].forge = {name!r} is not github, gitlab or auto") from None
    return forge_cls(repo)
