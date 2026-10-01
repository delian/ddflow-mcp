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


def _driver(rows: list[list[str]], glob: str) -> str:
    """The merge driver `.gitattributes` names for ``glob`` (`merge=<driver>`), or "".

    One pattern per line, the first token (gitattributes(5)); the LAST matching line
    wins, as in git. A bare `merge` or `-merge` names no driver -- the default text merge
    or none -- so it does not count as a strategy for a shared file.
    """
    found = ""
    for r in rows:
        if r[0] == glob:
            for a in r[1:]:
                if a.startswith("merge="):
                    found = a.split("=", 1)[1]
                elif a in ("merge", "-merge", "!merge"):
                    found = ""
    return found


def committed_append_only(repo: Path) -> list[str]:
    """`[lease] append_only_globs` from the COMMITTED `.ddflow/config.toml` only.

    `.gitattributes` is tracked and reaches every clone; a glob declared in the
    git-ignored local layer is one machine's choice and must not write a rule for all.
    """
    import tomllib

    p = Path(repo) / ".ddflow" / "config.toml"
    try:
        data = tomllib.loads(p.read_text("utf-8")) if p.exists() else {}
    except (OSError, tomllib.TOMLDecodeError):
        return []
    globs = data.get("lease", {}).get("append_only_globs", [])
    return [g for g in globs if isinstance(g, str) and g.strip()]


def sync_attributes(repo: Path) -> list[str]:
    """Append `<glob> merge=union` for each committed append-only glob that lacks a
    driver; the lines added.

    Idempotent and append-only (`adopt._append_once`): the project's own lines stay, and a
    glob already given a merge DRIVER is left alone -- the project chose one. Reads the
    committed config (`committed_append_only`), never the local layer.
    """
    from .adopt import _append_once

    added: list[str] = []
    for glob in committed_append_only(repo):
        if _driver(_attribute_lines(repo), glob):
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
        for g in committed_append_only(repo)
        if not _driver(rows, g)
    ]
    notes = [
        f"[lease] shared_globs has {g!r} with no merge strategy in .gitattributes: "
        f"parallel items will conflict on it at merge. If it is generated, regenerate it "
        f"after merging; if it is append-only, move it to append_only_globs (ddflow then "
        f"writes merge=union); or declare a driver yourself ('{g} merge=<driver>')."
        for g in cfg.lease.shared_globs
        if not _driver(rows, g)
    ]
    return problems, notes
