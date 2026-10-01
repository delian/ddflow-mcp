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


def drivers(repo: Path, glob: str) -> dict[str, str]:
    """{covered path: the merge driver git applies to it, or ""} for ``glob``.

    Asked of git (`git check-attr merge`), not read off the file: patterns match like
    gitignore and the LAST matching line wins across different patterns, so a later
    `*.md merge=ours` overrides `CHANGELOG.md merge=union` (review finding). A bare
    `merge` (`set`), `-merge` (`unset`) or nothing (`unspecified`) is no driver; a git
    that cannot answer gives every path "".
    """
    from ..infra import proc as P

    paths = _probe_paths(repo, glob)
    r = P.run(
        ["git", "-C", str(repo), "check-attr", "merge", "--", *paths],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return dict.fromkeys(paths, "")
    out: dict[str, str] = {}
    for ln in r.stdout.splitlines():
        if not ln.strip():
            continue
        path, _attr, value = ln.rsplit(": ", 2)
        out[path] = "" if value in ("unspecified", "set", "unset") else value
    return out or dict.fromkeys(paths, "")


def driver(repo: Path, glob: str) -> str:
    """The merge driver ``glob`` has as a whole: "" when any covered file has none;
    `union` when union applies to some file -- the rest carry narrower drivers the
    project set, which win by design (operator 2026-10-01); otherwise the project's
    driver for all of it."""
    values = list(drivers(repo, glob).values())
    if not values or "" in values:
        return ""
    if "union" in values:
        return "union"
    return values[0]


def _merge_rows(rows: list[str]) -> list[tuple[int, str]]:
    """(index, pattern) of every `.gitattributes` line that sets a merge attribute."""
    out = []
    for k, row in enumerate(rows):
        parts = row.split()
        if not parts or parts[0].startswith("#"):
            continue
        if any(a.startswith("merge=") or a in ("merge", "-merge", "!merge") for a in parts[1:]):
            out.append((k, parts[0]))
    return out


def _has_line(repo: Path, line: str) -> bool:
    path = Path(repo) / ".gitattributes"
    rows = path.read_text("utf-8").splitlines() if path.exists() else []
    return any(" ".join(r.split()) == line for r in rows)


def _placed(repo: Path, glob: str, line: str) -> tuple[list[str], list[str]]:
    """(the `.gitattributes` lines now, the lines with ``line`` where it belongs).

    It belongs BEFORE the first line that sets a driver for a NARROWER part of the glob
    -- some, not all, of the files it covers, or one literal file -- so that line keeps
    winning (git applies the LAST matching line; operator 2026-10-01: the narrower
    driver wins). A broader or equal line is not narrower and does not move it. An
    existing union line after a narrower one -- what 08af811 wrote -- is moved, not
    duplicated; one already in place is left alone.
    """
    from ..core.schedule import is_shared

    path = Path(repo) / ".gitattributes"
    rows = path.read_text("utf-8").splitlines() if path.exists() else []
    norm = [" ".join(r.split()) for r in rows]
    have = norm.index(line) if line in norm else -1
    covered = _probe_paths(repo, glob)
    last_broad, narrower = -1, []
    for k, pattern in _merge_rows(rows):
        if k == have:
            continue
        hit = [p for p in covered if p == pattern or is_shared(p, [pattern])]
        if not hit:
            continue
        literal = not any(ch in pattern for ch in "*?[")
        if pattern != glob and (len(hit) < len(covered) or literal):
            narrower.append(k)
        else:
            last_broad = k
    # A broader line wins over everything before it; the union line must follow the last
    # one, and precede the first narrower line after it. A narrower line BEFORE the last
    # broad one was already overridden by it, and moving lines around cannot revive it
    # without reordering the project's own rules -- which ddflow does not do.
    after = [k for k in narrower if k > last_broad]
    target = after[0] if after else None
    if have != -1 and have > last_broad and (target is None or have < target):
        return rows, rows  # present, and in place
    # Indices below are in ``rows``; removing an existing line before them shifts by one.
    at = target if target is not None else (last_broad + 1 if have != -1 else len(rows))
    new = [r for k, r in enumerate(rows) if k != have]
    new.insert(at - (1 if have != -1 and have < at else 0), line)
    return rows, new


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

    Idempotent; the project's own lines stay and keep working: the union line goes before
    any narrower line that sets a driver for a file inside the glob (the narrower driver
    wins), and a glob already given a merge DRIVER is left alone -- the project chose one. Reads the
    committed config (`committed_append_only`), never the local layer.
    """
    added: list[str] = []
    for glob in committed_append_only(repo):
        if any(ch.isspace() for ch in glob):
            continue  # one pattern per line, ended by whitespace: doctor says how to write it
        line = union_line(glob)
        d = driver(repo, glob)
        if d and not (d == "union" and _has_line(repo, line)):
            continue  # union from a line of the project's own, or a driver it chose
        rows, placed = _placed(repo, glob, line)
        if placed != rows:
            (Path(repo) / ".gitattributes").write_text("\n".join(placed) + "\n", "utf-8")
            added.append(line)
    return added


def findings(repo: Path, cfg: Config) -> tuple[list[str], list[str]]:
    """(problems, notes) for doctor.

    A PROBLEM: an append-only glob whose `merge=union` line is missing -- two items'
    lines will conflict at merge, the exact thing the setting promises not to happen --
    or whose union line sits after a narrower driver the project set, overriding it.
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
        rows, placed = (
            _placed(repo, g, union_line(g)) if _has_line(repo, union_line(g)) else ([], [])
        )
        if d == "union" and placed != rows:
            problems.append(
                f"[lease] append_only_globs has {g!r}, and its '{union_line(g)}' line comes "
                f"AFTER a narrower merge driver the project set, overriding it. `ddflow "
                f"init` (or setting the knob again) moves it before that line; commit it."
            )
        elif not d:
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
