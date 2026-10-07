"""Files many items may hold at once, and how they merge (D-shared-globs, B07878037ab).

Two kinds, declared under `[lease]`, both exempt from lease-overlap checks:

- `append_only_globs` -- a changelog, a research log. Every item adds lines, and git's
  `merge=union` keeps both sides' lines (probe R16b7d15f12: two prepends merge cleanly).
  ddflow WRITES `<glob> merge=union` to `.gitattributes` for each; the operator chose
  automatic over advice, since an advised line nobody added is a conflict at merge.
- `shared_globs` -- generated files. Union-merging one interleaves it into nonsense, so
  ddflow writes NOTHING for them; the remedy is to regenerate after merging, or to
  declare a driver. Doctor notes a shared glob with no merge attribute at all. A document
  edited by hand in sections (README.md, D-readme-current) is merged right by git's
  default text merge; `<glob> merge=text` says so, and counts as a strategy (git's
  built-in driver of that name -- `driver()` reads any value).

The line is written by `sync_attributes`, called where the setting changes -- the
`configure` api (CLI `config --set/--append-toml`, MCP `ddflow_configure`), in the same
tree as the config file, so both land in one commit -- and by `adopt.init_files`
(`init`, `adopt`), which re-syncs a config edited by hand. Each caller prints the lines
added; nothing is written twice.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config


def export_targets(cfg: Config, *, generated_only: bool = False) -> list[str]:
    """Repo-relative paths of every SELECTED export document (D-export-selection).

    ``generated_only`` leaves out `region` targets: those are hand-written files holding a
    generated region, so their own text is still scanned for stale mentions.

    They are shared paths already (`core.schedule.shared_globs` adds them, so no claim is
    needed and the lease check passes them); the whole and append ones are generated
    documents (``generated_only=True``): `ddflow export --check` judges them, so
    `[enforce].stale_docs` and docscheck leave them alone, and doctor's "no
    merge strategy" note (which reads only `[lease].shared_globs`) never names one.
    """
    return [
        p for _doc, p, mode in cfg.export.targets() if not (generated_only and mode == "region")
    ]


def doc_exclude(cfg: Config) -> list[str]:
    """`[enforce].doc_exclude` plus every whole or append export target (not a region
    one): a generated document is checked by `export --check`, not by the stale-mention
    scan."""
    out = list(cfg.enforce.doc_exclude)
    out += [p for p in export_targets(cfg, generated_only=True) if p not in out]
    return out


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

    if not any(ch in glob for ch in "*?["):
        return [glob]
    listed = _git_z(repo, "ls-files") or []
    return [p for p in listed if is_shared(p, [glob])] or [glob]


def _git_z(repo: Path, *args: str) -> list[str] | None:
    """`W.git_paths` -- `-z`, read as bytes, so a non-ASCII path comes back as the file
    is named, not C-quoted (B9c56de9d58) -- and None when git could not run or did not
    answer in time, never an exception out of doctor or a config write."""
    from ..infra import git as G
    from ..infra import proc as P

    return G.git_paths(repo, *args, timeout=P.TIMEOUTS["probe"])


def drivers(repo: Path, glob: str) -> dict[str, str]:
    """{covered path: the merge driver git applies to it, or ""} for ``glob``.

    Asked of git (`git check-attr merge`), not read off the file: patterns match like
    gitignore and the LAST matching line wins across different patterns, so a later
    `*.md merge=ours` overrides `CHANGELOG.md merge=union` (review finding). A bare
    `merge` (`set`), `-merge` (`unset`) or nothing (`unspecified`) is no driver; a git
    that cannot answer gives every path "".
    """
    paths = _probe_paths(repo, glob)
    # With -z each answer is `<path> NUL <attribute> NUL <value> NUL`: the path as named.
    fields = _git_z(repo, "check-attr", "merge", "--", *paths)
    if fields is None or len(fields) % 3:
        return dict.fromkeys(paths, "")
    out: dict[str, str] = {}
    for k in range(0, len(fields), 3):
        path, value = fields[k], fields[k + 2]
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


def _split_pattern(row: str) -> tuple[str, list[str]]:
    """(pattern, attributes) of one `.gitattributes` line, as git reads it.

    A pattern may be C-quoted (`"docs/My File.md" merge=ours`) or hold `\\ ` escapes;
    a plain `split()` cut it at the space and the rule was never recognised (review).
    """
    row = row.strip()
    if row.startswith('"'):
        out, i = [], 1
        while i < len(row) and row[i] != '"':
            if row[i] == "\\" and i + 1 < len(row):
                i += 1
            out.append(row[i])
            i += 1
        return "".join(out), row[i + 1 :].split()
    out, i = [], 0
    while i < len(row) and not row[i].isspace():
        if row[i] == "\\" and i + 1 < len(row):
            i += 1
        out.append(row[i])
        i += 1
    return "".join(out), row[i:].split()


def _merge_rows(rows: list[str]) -> list[tuple[int, str, str]]:
    """(index, pattern, merge value) of every `.gitattributes` line setting a merge
    attribute; the value is the driver after `merge=`, or the bare form itself."""
    out = []
    for k, row in enumerate(rows):
        if not row.strip() or row.lstrip().startswith("#"):
            continue
        pattern, attrs = _split_pattern(row)
        for a in attrs:
            if a.startswith("merge="):
                out.append((k, pattern, a.split("=", 1)[1]))
            elif a in ("merge", "-merge", "!merge"):
                out.append((k, pattern, a))
    return out


def _witness(pattern: str) -> str:
    """A stand-in path for ``pattern``: `**` -> two segments, `*`/`?`/a class -> U+0001,
    which no literal holds. It is matched by `*`, `?`, `**` and a NEGATED class, NOT by a
    positive class -- so a pattern with one may read "not inside" a true superset, and
    the caller then writes an inert union line rather than a wrong one. Read as a path, `docs/**` was matched by `docs/*`
    (a `*` matches `**`); a plain `x` was matched by a narrower `docs/x*` (review
    findings). The stand-in is matched only by wildcards -- including a NEGATED class
    (`[!x]`), which is why `_relation` treats containment both ways as ambiguous."""
    import re

    w = re.sub(r"\[[^]]*\]", "\x01", pattern)
    return w.replace("**", "\x01/\x01").replace("*", "\x01").replace("?", "\x01")


def _inside(a: str, b: str) -> bool:
    """Is every file pattern ``a`` names also named by ``b``? (Judged on a witness of
    ``a``: exact for literals; for wildcards, a path only another wildcard matches.)"""
    from ..core.schedule import is_shared

    return a == b or is_shared(_witness(a), [b])


def _relation(glob: str, pattern: str) -> str:
    """ "broader", "narrower" or "" (unknown) for ``pattern`` against ``glob``.

    Containment one way only. Both ways while different (`docs/[!x]*` vs `docs/*`: a
    negated class matches the stand-in too) is ambiguous, and left to the files that
    exist (review finding) -- never assumed broader, which wrote no union line at all.
    """
    if pattern == glob:
        return "broader"
    up, down = _inside(glob, pattern), _inside(pattern, glob)
    if up and not down:
        return "broader"
    if down and not up:
        return "narrower"
    return ""


def _broad_rule(repo: Path, glob: str) -> bool:
    """Does a `.gitattributes` merge rule cover the WHOLE glob -- the glob itself, or a
    pattern it falls inside (`*.md` for `docs/*.md`)? Then the project chose for all of
    it -- by `_relation`, so only one-way containment counts. A narrower rule
    (`docs/README.md`, `docs/R*.md`) or an ambiguous one chose for part only."""
    path = Path(repo) / ".gitattributes"
    rows = path.read_text("utf-8").splitlines() if path.exists() else []
    return any(_relation(glob, p) == "broader" for _k, p, _v in _merge_rows(rows))


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
    for k, pattern, value in _merge_rows(rows):
        if k == have:
            continue
        # By the patterns themselves first (`_inside`): `docs/R*.md` is narrower than
        # `docs/*.md` whatever files exist today (review finding), `*.md` is broader. A
        # broader UNION rule changes nothing for us wherever our line sits, so it does
        # not pull the line after it.
        rel = _relation(glob, pattern)
        if rel == "broader":
            if value != "union":
                last_broad = k
            continue
        if rel == "narrower":
            narrower.append(k)
            continue
        # Not positively broader: a rule that covers files the glob covers today, or
        # whose relation is ambiguous, is treated as the project's narrower choice and
        # keeps winning -- full coverage of TODAY's files is no proof it is broader
        # (review finding: `docs/[!x]*` over only `docs/guide.md`).
        ambiguous = _inside(glob, pattern) and _inside(pattern, glob)
        if ambiguous or any(p == pattern or is_shared(p, [pattern]) for p in covered):
            narrower.append(k)
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
    wins). A glob whose WHOLE extent a project rule already covers (the glob itself, or a
    broader pattern) is left alone when that rule is not union -- the project chose; a
    rule covering only part of the glob does not stop the union line. Reads the
    committed config (`committed_append_only`), never the local layer.
    """
    added: list[str] = []
    for glob in committed_append_only(repo):
        if any(ch.isspace() for ch in glob):
            continue  # one pattern per line, ended by whitespace: doctor says how to write it
        line = union_line(glob)
        d = driver(repo, glob)
        if d and _broad_rule(repo, glob) and (d != "union" or not _has_line(repo, line)):
            continue  # the project's own rule covers the whole glob: its call, or union already
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
        f"[lease] shared_globs has {g!r} with no merge strategy in .gitattributes. A "
        f"generated file will conflict on it at merge: regenerate it after merging; an "
        f"append-only one belongs in append_only_globs (ddflow then writes merge=union). "
        f"A document items edit by hand in different sections (a README) merges cleanly "
        f"under git's default text merge: say so with '{g} merge=text'."
        for g in cfg.lease.shared_globs
        if not driver(repo, g)
    ]
    return problems, notes
