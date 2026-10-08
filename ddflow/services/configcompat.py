"""What an older ddflow skipped or ignored in the config files (D-compat 2).

`ddflow doctor` lists it for EVERY table, not only on stderr at load: unknown keys and
values (problems), fields of `[gate.*]`, `[[reviewer]]`, `[[companion]]` and `[[macro]]`
that a loader skips (problems), members of a checked list ignored (notes) and a config
`format` newer than this code (a note). The remedy fits how this ddflow is installed.
"""

from __future__ import annotations

from pathlib import Path

from ..config import CONFIG_FORMAT, Config, _is_code_tree
from ..infra import tomlcfg as TC
from . import install_info as II
from .companions import Companion
from .gates.defs import GateDef
from .macros import Macro
from .review import Reviewer

#: The tables whose fields a loader skips: (own file, table, the class that knows the
#: fields, `[[array]]`?, the field naming an entry, its fallback).
FIELD_TABLES = (
    ("gates.toml", "gate", GateDef, False, "", ""),
    ("reviewers.toml", "reviewer", Reviewer, True, "name", "model"),
    ("companions.toml", "companion", Companion, True, "id", ""),
    ("macros.toml", "macro", Macro, True, "name", ""),
)


def skipped_table_fields(repo: Path) -> list[tuple[str, list[str]]]:
    """`(where, fields)` the loaders of `[gate.*]`, `[[reviewer]]`, `[[companion]]` and
    `[[macro]]` skip for a file a newer ddflow wrote. In ddflow's own source tree a committed
    file's unknown field is an error on load, so only the machine-local layer is read there."""
    own = _is_code_tree(repo)
    out: list[tuple[str, list[str]]] = []
    for file, table, cls, array, key, fallback in FIELD_TABLES:
        paths = [
            p
            for p in TC.config_paths(repo, file)
            if not own or (p.parent.name == "local" and p.parent.parent.name == ".ddflow")
        ]
        out += TC.skipped_fields(paths, table, cls, array=array, key=key, fallback_key=fallback)
    return out


def report(repo: Path, cfg: Config, problems: list[str], notes: list[str]) -> None:
    """Append what the config files hold that this ddflow skipped or ignored to ``problems``
    and ``notes`` (the lists `doctor` builds)."""
    fix = ""

    def advice() -> str:
        nonlocal fix
        fix = fix or II.upgrade_advice()
        return fix

    bad_values = cfg.invalid_value_indices()
    problems += [
        f"{'invalid value for' if i in bad_values else 'unknown config key'} "
        f"{k} in .ddflow/config.toml: a typo, or written by a newer ddflow than this "
        f"one runs ({advice()})"
        for i, k in enumerate(cfg.unknown_knobs)
    ]
    problems += [
        f"{where}: field(s) {', '.join(extra)} skipped -- a typo, or written by a newer "
        f"ddflow than this one runs ({advice()})"
        for where, extra in skipped_table_fields(repo)
    ]
    notes += [f"config: {m}; the rest of the setting applies" for m in cfg.ignored_members]
    if cfg.file_format > CONFIG_FORMAT:
        notes.append(
            f"config is format {cfg.file_format}, newer than the format {CONFIG_FORMAT} this "
            f"ddflow understands: what it knows is applied, and writes to it are refused "
            f"({advice()})"
        )
