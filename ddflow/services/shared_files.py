"""Files many items may hold at once, and how they merge (D-shared-globs, B07878037ab).

Two kinds, declared under `[lease]`, both exempt from lease-overlap checks:

- `append_only_globs` -- a changelog, a research log. Every item adds lines, and git's
  `merge=union` keeps both sides' lines (probe R16b7d15f12: two prepends merge cleanly).
  ddflow WRITES `<glob> merge=union` to `.gitattributes` for each; the operator chose
  automatic over advice, since an advised line nobody added is a conflict at merge.
- `shared_globs` -- generated files. Union-merging one interleaves it into nonsense, so
  ddflow writes NOTHING for them; the remedy is to regenerate after merging, or to
  declare a driver. Doctor notes a shared glob with no merge attribute at all.

The line is written by `sync_attributes`, called where the setting changes -- the
`configure` api (CLI `config --set/--append-toml`, MCP `ddflow_configure`), in the same
tree as the config file, so both land in one commit -- and by `adopt.init_files`
(`init`, `adopt`), which re-syncs a config edited by hand. Each caller prints the lines
added; nothing is written twice.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config


def union_line(glob: str) -> str:
    return f"{glob} merge=union"


def _attribute_lines(repo: Path) -> list[list[str]]:
    p = Path(repo) / ".gitattributes"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text("utf-8").splitlines():
        parts = line.split()
        if parts and not parts[0].startswith("#"):
            rows.append(parts)
    return rows


def _has(rows: list[list[str]], glob: str, attr_prefix: str) -> bool:
    return any(r[0] == glob and any(a.startswith(attr_prefix) for a in r[1:]) for r in rows)


def sync_attributes(repo: Path, cfg: Config) -> list[str]:
    """Append `<glob> merge=union` for each append-only glob that lacks it; the lines added.

    Idempotent and append-only (`adopt._append_once`): the project's own lines stay, and a
    glob already given ANY merge attribute is left alone -- the project chose one.
    """
    from .adopt import _append_once

    added: list[str] = []
    for glob in cfg.lease.append_only_globs:
        if _has(_attribute_lines(repo), glob, "merge"):
            continue
        line = union_line(glob)
        if _append_once(Path(repo) / ".gitattributes", frozenset({line}), line + "\n"):
            added.append(line)
    return added


def findings(repo: Path, cfg: Config) -> tuple[list[str], list[str]]:
    """(problems, notes) for doctor.

    A PROBLEM: an append-only glob whose `merge=union` line is missing -- two items'
    lines will conflict at merge, the exact thing the setting promises not to happen.
    A NOTE: a shared (generated) glob with no merge attribute -- not wrong, but every
    parallel merge of it will conflict until someone regenerates it.
    """
    rows = _attribute_lines(repo)
    problems = [
        f"[lease] append_only_globs has {g!r} but .gitattributes has no merge attribute "
        f"for it, so parallel items' lines conflict at merge. `ddflow init` (or setting "
        f"the knob again with `ddflow config --set`) writes '{union_line(g)}'; commit it."
        for g in cfg.lease.append_only_globs
        if not _has(rows, g, "merge")
    ]
    notes = [
        f"[lease] shared_globs has {g!r} with no merge strategy in .gitattributes: "
        f"parallel items will conflict on it at merge. If it is generated, regenerate it "
        f"after merging; if it is append-only, move it to append_only_globs (ddflow then "
        f"writes merge=union); or declare a driver yourself ('{g} merge=<driver>')."
        for g in cfg.lease.shared_globs
        if not _has(rows, g, "merge")
    ]
    return problems, notes
