"""Keep git's background maintenance out of the suite (B4bb3c23c45).

`git push` into a bare repository, `git commit` and `git fetch` start `git maintenance run
--auto` (and `gc --auto`), which takes `objects/maintenance.lock` in that repository while it
runs. Many tests push into a bare "remote" and clone it at once; git's local clone copies the
remote's `objects/` directory file by file, and a lock file that disappears between the
listing and the copy ends the clone with ``fatal: failed to copy file to
.../objects/maintenance.lock: No such file or directory`` (seen once in the operator's pre-push
run, 11763 passed and 1 setup error; the test passes alone). A test repository never needs
maintenance, so it is switched off for every git process the suite starts.

Uses ``GIT_CONFIG_COUNT`` (git 2.31+), appended after any entries the environment already
carries so a developer's own settings are kept.
"""

from __future__ import annotations

from collections.abc import MutableMapping

QUIET = (
    ("gc.auto", "0"),
    ("maintenance.auto", "false"),
    ("receive.autogc", "false"),
)


def quiet_git(env: MutableMapping[str, str]) -> None:
    """Add the QUIET settings to ``env`` (idempotent), after any ``GIT_CONFIG_COUNT`` entries."""
    try:
        n = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    except ValueError:
        n = 0
    have = {env.get(f"GIT_CONFIG_KEY_{i}") for i in range(n)}
    for key, value in QUIET:
        if key in have:
            continue
        env[f"GIT_CONFIG_KEY_{n}"] = key
        env[f"GIT_CONFIG_VALUE_{n}"] = value
        n += 1
    env["GIT_CONFIG_COUNT"] = str(n)
