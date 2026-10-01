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


def _probe_paths(repo: Path, glob: str) -> list[str]:
    """The paths to ask git about for ``glob``: the glob itself when it is a literal path,
    else every tracked file it matches, else the glob's own name.

    A character class does not match its own name (`[Cc]HANGELOG.md` names no `[`), so
    asking git about the pattern string reported "no driver" for a working line. And one
    sample is not enough: a driver the project set on `docs/README.md` says nothing about
    `docs/guide.md` under the same `docs/*.md` (review findings).
    """
    from ..core.schedule import is_shared
    from ..infra import proc as P

    if not any(ch in glob for ch in "*?["):
        return [glob]
    r = P.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True)
    hits = [p for p in r.stdout.splitlines() if is_shared(p, [glob])] if r.returncode == 0 else []
    return hits or [glob]


def driver(repo: Path, glob: str) -> str:
    """The merge driver git applies to EVERY file ``glob`` covers -- `union`, `ours`, ...
    -- or "" when any of them has none.

    Asked of git (`git check-attr merge`), not read off the file: patterns match like
    gitignore and the LAST matching line wins across different patterns, so a later
    `*.md merge=ours` overrides `CHANGELOG.md merge=union` (review finding). A bare
    `merge` (`set`), `-merge` (`unset`) or nothing (`unspecified`) is no driver; so is a
    git that cannot answer. When the covered files disagree, `union` is reported only if
    all are union, and otherwise the first other driver found.
    """
    from ..infra import proc as P

    paths = _probe_paths(repo, glob)
    r = P.run(
        ["git", "-C", str(repo), "check-attr", "merge", "--", *paths],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return ""
    values = [ln.rsplit(": ", 1)[-1] for ln in r.stdout.splitlines() if ln.strip()]
    if not values or any(v in ("unspecified", "set", "unset") for v in values):
        return ""
    others = [v for v in values if v != "union"]
    return others[0] if others else "union"


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
    if isinstance(globs, str):
        globs = [globs]  # hand-written as one string: iterated, it wrote a rule per letter
    if not isinstance(globs, list):
        return []
    return [g for g in globs if isinstance(g, str) and g.strip()]


def sync_attributes(repo: Path) -> list[str]:
    """Append `<glob> merge=union` for each committed append-only glob that lacks a
    driver; the lines added. A glob holding whitespace is skipped -- a pattern ends at the
    first space -- and `findings` says how to write it.

    Idempotent and append-only (`adopt._append_once`): the project's own lines stay, and a
    glob already given a merge DRIVER is left alone -- the project chose one. Reads the
    committed config (`committed_append_only`), never the local layer.
    """
    from .adopt import _append_once

    added: list[str] = []
    for glob in committed_append_only(repo):
        if any(ch.isspace() for ch in glob):
            continue  # one pattern per line, ended by whitespace: doctor says how to write it
        if driver(repo, glob):
            continue  # union already, or a driver the project chose -- doctor says which
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
    import re

    from ..core.schedule import _gitattributes_re

    problems: list[str] = []
    notes: list[str] = []
    for g in [*cfg.lease.shared_globs, *cfg.lease.append_only_globs]:
        try:
            _gitattributes_re(g)
        except re.error as exc:
            # `is_shared` treats it as matching only itself rather than crash a claim;
            # this is where that is said out loud.
            problems.append(
                f"[lease] shared glob {g!r} cannot be read as git reads it ({exc}); it is "
                f"treated as matching only itself. Rewrite it (e.g. an ascending range)."
            )
    for g in committed_append_only(repo):
        if any(ch.isspace() for ch in g):
            problems.append(
                f"[lease] append_only_globs has {g!r}: a .gitattributes pattern ends at the "
                f"first space, so no union line can be written for it. Write the space as ? "
                f"(e.g. {g.replace(' ', '?')!r})."
            )
            continue
        d = driver(repo, g)
        if not d:
            problems.append(
                f"[lease] append_only_globs has {g!r} but git applies no merge driver to "
                f"it, so parallel items' lines conflict at merge. `ddflow init` (or setting "
                f"the knob again with `ddflow config --set`) writes '{union_line(g)}'; "
                f"commit it."
            )
        elif d != "union":
            notes.append(
                f"[lease] append_only_globs has {g!r}, but .gitattributes gives it "
                f"merge={d}, not union: parallel items' added lines may be dropped or "
                f"conflict. ddflow leaves a driver the project chose alone."
            )
    notes += [
        f"[lease] shared_globs has {g!r} with no merge strategy in .gitattributes: "
        f"parallel items will conflict on it at merge. If it is generated, regenerate it "
        f"after merging; if it is append-only, move it to append_only_globs (ddflow then "
        f"writes merge=union); or declare a driver yourself ('{g} merge=<driver>')."
        for g in cfg.lease.shared_globs
        if not driver(repo, g)
    ]
    return problems, notes
