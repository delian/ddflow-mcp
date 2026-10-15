"""The `[pins]` section: where the pin recipes live and how a regeneration behaves.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob

#: What `[pins].after_merge` accepts.
PINS_AFTER_MERGE = ("amend", "leave", "off")


@declare("pins")
@dataclass
class PinsConfig:
    """Pin files (golden snapshots, transcripts, generated tables) that regenerate themselves."""

    recipes_file: str = knob(
        ".ddflow/pins.toml",
        doc="The project's pin recipes, as data: one `[[pin]]` table per kind of pin file with `name`, `files` (the pin files it owns), `check` (an argv; exit 0 = the pin is current) and `regen` (an argv that re-records it, touching only what differs), and optionally `env` (a table of environment variables given to `regen` only, never to `check`, so a check can not re-record what it is checking). `{python}` in an argv is this interpreter. `ddflow pins regenerate [--check] [--only NAME]` runs them; a new kind of pin is a new table, no code. Relative to the repository root.",
    )
    timeout_s: int = knob(
        900,
        doc="Seconds one recipe's check or regeneration may run before it is stopped and reported as failed.",
        check=int_at_least(1),
    )
    after_merge: str = knob(
        "amend",
        doc="What the post-merge hook installed by `ddflow pins regenerate --install` does after a merge: 'amend' (regenerate the stale pins and fold them into the merge commit when nothing else is dirty), 'leave' (regenerate and leave the files changed for you to commit) or 'off' (do nothing).",
        choices=PINS_AFTER_MERGE,
        strictest=(
            "leave",
            "never rewrites a commit; the regenerated files are left for you to commit",
        ),
    )
