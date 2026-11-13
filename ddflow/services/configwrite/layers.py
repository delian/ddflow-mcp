"""Machine-local layer classification and the file a writer targets.

Split out of ``ddflow/services/configwrite.py``. Where "local" is, and which keys
default there, is one decision the whole write path must agree on: a writer that
guessed its own path is how a reviewer endpoint ended up in the committed config
while the reader looked for it under `local/` (bug B-reviewers-write-committed).
"""

from __future__ import annotations

from pathlib import Path

from ...infra import fsio
from ...infra import tomlcfg as TC

#: The git-ignored machine-local layer (decision D-no-own-services-local-dir). Read
#: LAST by `Config.load` and `tomlcfg.config_paths`, so what is written here wins.
LOCAL_DIR = TC.LOCAL_DIR

#: Machine-local keys and the layer their writes default to (DX-machine-local-keys, the
#: follow-up of bug B65bbc8083c): what ONE machine answers for itself -- how many workers
#: it runs, how it reaches its reviewers and endpoints -- is not the next machine's
#: answer, so `config KEY VALUE` stops dropping it into the committed file unless the
#: operator deliberately sends it there. `[enforce].machine_local_keys` extends this table
#: (a knob, config_sections/enforce.py), the one place it is held so a knob's default
#: layer is stated where the write is applied.
MACHINE_LOCAL_KEYS: dict[str, str] = {
    # Parallelism is machine sizing: the adaptive limit is "derived on this machine, never
    # committed", and the schedule docs already say "set it in the local layer".
    "schedule.parallel": "local",
    "schedule.max_parallel_tasks": "local",
    "schedule.max_parallel_min": "local",
    "schedule.max_parallel_max": "local",
    # Reviewers and companion endpoints are this machine's services: a `[[reviewer]]` or
    # `[[companion]]` block names a host and port that must never reach the committed
    # file (the same facts that sent `[[reviewer]]` to the local reviewers.toml in
    # bug B-reviewers-write-committed). They arrive as tables (`_appended_keys`), so the
    # name here classifies the table itself and every dotted key under it; the `--set`
    # surface never writes them (no `<section>.<knob>` under them is a knob).
    "reviewer": "local",
    "companion": "local",
    # The two tables that decide what a paired token may do: operator-owned, so a clone's
    # committed file cannot widen a role (B-token-role-widened). Their writes default to the
    # git-ignored local layer, like the reviewer/companion endpoints above.
    "agent.roles": "local",
    "agent.capability_verbs": "local",
}


def machine_local_layer(extra: set[str], dotted: str) -> str:
    """The layer a write of ``dotted`` defaults to: the layer its classification names
    ("local" here, or in `[enforce].machine_local_keys`), else "" -- the committed layer.
    A table name classifies the table and everything under it: `reviewer` names
    `[[reviewer]]` blocks as well as a dotted `reviewer.x`. The mapping's VALUE is the
    layer, so reclassifying a key edits one table, never this function."""
    head = dotted.partition(".")[0]
    if dotted in MACHINE_LOCAL_KEYS:
        return MACHINE_LOCAL_KEYS[dotted]
    if head in MACHINE_LOCAL_KEYS:
        return MACHINE_LOCAL_KEYS[head]
    return "local" if dotted in extra or head in extra else ""


def machine_local_tables(extra: set[str], names: set[str]) -> bool:
    """Whether EVERY top-level table ``names`` names is machine-local: classified (with a
    non-empty layer, the value-as-layer rule) or extended by `[enforce].machine_local_keys`.
    Used to default an appended TOML document that only configures this machine's
    reviewers or companions to the local layer."""
    return bool(names) and all(
        (name in MACHINE_LOCAL_KEYS and bool(MACHINE_LOCAL_KEYS[name])) or name in extra
        for name in names
    )


def config_file(repo: Path, *, local: bool = False, own: str = "config.toml") -> Path:
    """The file a writer targets: the committed `.ddflow/<own>`, or its local twin.

    One function so every writer agrees on where "local" is. A writer that guessed its
    own path is how a reviewer endpoint ended up in the committed config while the
    reader looked for it under `local/` (bug B-reviewers-write-committed).
    """
    return TC.layer_path(repo, "local" if local else "file", own)


def ensure_local_dir(repo: Path) -> Path:
    """Create `.ddflow/local/` with its own `*` `.gitignore`, and return it.

    The directory ignores itself rather than trusting `.ddflow/.gitignore`: a project
    whose ignore file predates `local/`, or that never ran `init`, must still not commit
    the endpoint, key variable or worker count it is about to receive.
    """
    return fsio.ensure_ignored_dir(Path(repo) / LOCAL_DIR)
