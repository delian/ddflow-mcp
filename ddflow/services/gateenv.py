"""The environment a gate command or the CI command is given.

A shell that exports `DDFLOW_AGENT` (every fleet agent's does) handed it to the project's
test command, and three `test_clone_id_recovery` tests that read the caller's identity failed
in the gate yet passed with the variable unset (B3a8e04ca38). The identity variables ddflow
reads are the caller's, not the suite's, so they are removed from the child's environment:
`[gates].scrub_env` names them (plus any the project adds), `[gates].keep_env` and
`[ci].keep_env` list the ones to preserve.

The caller's secrets are the second half (SEC-gate-env-secrets). Reviewer keys are exported into
the operator's shell before `ddflow review`, and a gate command is project code run through a
shell: it can read and print them, and its output lands in the committed log. A variable whose
NAME matches `[gates].secret_name_patterns` (case-blind: `_API_KEY`, `_TOKEN`, ...) is named in the
gate's evidence (`env_secret_names`, never a value) under `[gates].scrub_secret_names = warn`, the
default, and removed from the child under `on`; `off` does neither. `keep_env` preserves one.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

from ..config import Config
from ..config_sections.gates import GatesConfig

#: ddflow's own environment settings are never secret-shaped.
OWN_PREFIX = "DDFLOW_"

#: Names `doctor` lists before it says "(+N more)".
MAX_NAMES_SHOWN = 8

#: Variables that make git ignore the directory it is told to work in. A gate run from a git
#: hook inherits them, and then every `git init` / `git -C tmp config` / `git -C tmp commit`
#: of the project's tests acts on the repository running the gate instead of the test's own
#: (B1d82c6b76f: a stray identity, core.bare = true and "init" commits in the real repo).
#: Removed whatever `scrub_env` says; `keep_env` still preserves one.
REPO_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_QUARANTINE_PATH",
    "GIT_INTERNAL_SUPER_PREFIX",
)


def secret_names(
    base: Mapping[str, str], gates: GatesConfig, keep: Iterable[str] = ()
) -> list[str]:
    """The names in ``base`` that look like secrets (a case-blind pattern of
    `[gates].secret_name_patterns` is part of the name), without the kept ones and without those
    `scrub_env` or `REPO_VARS` already remove, and without ddflow's own settings (`DDFLOW_*`:
    `DDFLOW_GATES_SCRUB_SECRET_NAMES` contains `_SECRET`, and a nested ddflow must keep it). Names only: a value never passes through here."""
    pats = [p.upper() for p in gates.secret_name_patterns if p]
    skip = set(keep) | set(gates.keep_env) | set(gates.scrub_env) | set(REPO_VARS)
    return sorted(
        k
        for k in base
        if k not in skip
        and not k.upper().startswith(OWN_PREFIX)
        and any(p in k.upper() for p in pats)
    )


def env_evidence(
    cfg: Config | None, base: Mapping[str, str] | None = None, *, ci: bool = False
) -> dict[str, Any]:
    """What a gate's evidence says about the secret-shaped variables: the mode used, and (unless
    `off`) their NAMES: the visible ones under `warn`, the removed ones under `on`."""
    gates = cfg.gates if cfg is not None else GatesConfig()
    out: dict[str, Any] = {"env_secret_mode": gates.scrub_secret_names}
    if gates.scrub_secret_names != "off":
        keep = cfg.ci.keep_env if ci and cfg is not None else ()
        out["env_secret_names"] = secret_names(os.environ if base is None else base, gates, keep)
    return out


def doctor_notes(st: Any, cfg: Config) -> list[str]:
    """One note naming the secret-shaped variables gate runs recorded as visible to the command
    (`env_secret_names` under `warn`); empty once the project scrubs them or turns the check off."""
    if cfg.gates.scrub_secret_names != "warn":
        return []
    seen: set[str] = set()
    for it in st.items.values():
        for g in it.gates.values():
            ev = g.evidence if isinstance(getattr(g, "evidence", None), dict) else {}
            if ev.get("env_secret_mode") == "warn":
                seen.update(n for n in ev.get("env_secret_names") or [] if isinstance(n, str))
    if not seen:
        return []
    shown = sorted(seen)[:MAX_NAMES_SHOWN]
    more = f" (+{len(seen) - len(shown)} more)" if len(seen) > len(shown) else ""
    return [
        f"gate commands ran with secret-shaped variables in their environment: {', '.join(shown)}{more}; "
        "`ddflow config --set gates.scrub_secret_names on` removes them from every gate child and the CI "
        "command ([gates].keep_env preserves one, [ci].keep_env one for CI)"
    ]


def child_env(
    cfg: Config | None = None,
    base: Mapping[str, str] | None = None,
    *,
    ci: bool = False,
) -> dict[str, str]:
    """``base`` (default `os.environ`) without the scrubbed variables and `REPO_VARS`, minus the
    kept ones; under `[gates].scrub_secret_names = on` also without the secret-shaped names."""
    gates = cfg.gates if cfg is not None else GatesConfig()
    keep: set[str] = set(gates.keep_env)
    if ci and cfg is not None:
        keep |= set(cfg.ci.keep_env)
    env = os.environ if base is None else base
    scrub = (set(gates.scrub_env) | set(REPO_VARS)) - keep
    if gates.scrub_secret_names == "on":
        scrub |= set(secret_names(env, gates, keep))
    return {k: v for k, v in env.items() if k not in scrub}
