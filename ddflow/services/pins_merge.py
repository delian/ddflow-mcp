"""The git side of pin regeneration: a merge driver and a post-merge hook (DX-pins-regenerate).

Two branches that each re-record the same pin file conflict in it. The driver merges the prose
of a mixed file normally and keeps our side of the generated regions, so the merge does not stop
over them (the tree is not complete while it runs, so it cannot regenerate); a prose conflict
both sides made stays a conflict. The post-merge hook, which runs once the tree is final,
regenerates the stale pins and folds them into the merge commit.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import Config
from ..infra import git as G
from ..infra.fsio import atomic_write, replace_text
from . import shared_files as SF
from .enforce import command_line, hooks_dir
from .pins import load_recipes, pin_files, regenerate
from .pins_text import merge_into

#: The merge driver's name in `.gitattributes` and `git config merge.<name>.driver`.
DRIVER = "pinsregen"
HOOK_MARK = "# DDFLOW-PINS-HOOK v1 -- managed by `ddflow pins regenerate --install`"


def merge_driver(paths: str | list[str]) -> int:
    """`git merge` driver for a pin file (the file at `ours` is the result): the prose of the
    three versions merged, our side of the generated regions kept; the post-merge hook then
    re-records every stale pin. ``paths`` is what git passes (`%A %O %B`: ours, the common
    ancestor, theirs); a lone path keeps ours. 1 (a conflict) only for prose both sides edited."""
    given = [paths] if isinstance(paths, str) else list(paths)
    ours, base, theirs = [*given, "", "", ""][:3]
    if not Path(ours or "-").exists():
        return 1
    if not (base and theirs and Path(base).exists() and Path(theirs).exists()):
        return 0
    return 1 if merge_into(Path(ours), Path(base), Path(theirs)) else 0


def _fold_into_merge(repo: Path, files: list[str], done: list[str]) -> str:
    """Write the merge commit again with the regenerated pin files in it.

    `git commit --amend` is refused while MERGE_HEAD still exists, which it does in the
    post-merge hook: the commit is written with plumbing and the branch moved to it."""
    added = G.run(repo, "add", "-A", "--", *files)
    tree = G.run(repo, "write-tree")
    head = G.run(repo, "rev-parse", "HEAD")
    parents = G.run(repo, "rev-list", "--parents", "-n", "1", "HEAD").out.split()[1:]
    msg = G.run(repo, "log", "-1", "--format=%B", "HEAD")
    steps = (added, tree, head, msg)
    bad = next((r for r in steps if not r.ok), None)
    # `commit-tree` ignores commit.gpgsign, which `git merge` honoured for the commit it replaces.
    # The commit being replaced decides (`git merge -S` signs without any config); the config
    # is the fallback for a merge whose signature cannot be read back.
    was = G.run(repo, "log", "-1", "--format=%G?", "HEAD").out.strip()
    cfgd = G.run(repo, "config", "--type=bool", "--get", "commit.gpgsign").out.strip() == "true"
    signed = (was not in ("", "N")) if was else cfgd
    new = bad or G.run(
        repo,
        "commit-tree",
        *(["-S"] if signed else []),
        tree.out.strip(),
        *[x for p in parents for x in ("-p", p)],
        input=msg.out + "\n",
    )
    if new.ok and G.run(repo, "update-ref", "HEAD", new.out.strip(), head.out.strip()).ok:
        return f"pins regenerated into the merge commit: {', '.join(done)}"
    return f"pins regenerated, not committed ({new.err}): {', '.join(done)}"


def _say(line: str) -> None:
    """Progress from the hook goes to stderr, where git shows a hook's output."""
    print(line, file=sys.stderr, flush=True)


def after_merge(repo: Path, cfg: Config) -> str:
    """Post-merge: regenerate; fold the result into the merge commit when only pin files
    changed. Returns one line saying what happened."""
    mode = cfg.pins.after_merge
    if mode == "off":
        return ""
    rep = regenerate(repo, cfg, after_merge=True, on_progress=_say)
    cannot = (
        "pins unavailable (not checked, nothing recorded as stale or failed): "
        + "; ".join(f"{p.name}: {p.unavailable}" for p in rep.unavailable)
        if rep.unavailable
        else ""
    )
    if rep.problem:  # a run that could not start says so; silence reads as "all current"
        return "; ".join(filter(None, [f"pins: {rep.problem}", cannot]))
    if not rep.pins:
        return ""  # nothing the hook may run
    if rep.failed:
        failed = "pins: " + "; ".join(f"{p.name}: {p.error}" for p in rep.failed)
        return "; ".join(filter(None, [failed, cannot]))
    done = [p.name for p in rep.pins if p.regenerated]
    if not done:
        return cannot
    files = pin_files(repo, cfg)
    paths = G.run(repo, "diff", "--name-only", "-z", "HEAD", z=True).paths() or []
    own = tuple(f.rstrip("/") for f in files)
    merge = G.run(repo, "rev-parse", "-q", "--verify", "HEAD^2").ok
    if mode == "amend" and merge and paths and all(p.startswith(own) for p in paths):
        return "; ".join(filter(None, [_fold_into_merge(repo, files, done), cannot]))
    return "; ".join(
        filter(None, [f"pins regenerated, not committed: {', '.join(done)}; commit them", cannot])
    )


def merge_report(repo: Path, cfg: Config) -> dict[str, list[str]]:
    """What `ddflow merge` says about the pins once a merge has landed (bug Bd92e5da53c): the
    pins the hook may run are CHECKED now (read-only, each bounded by
    `[pins].after_merge_timeout_s`), whether or not the hook is installed or ran, and the pins
    it never runs (a recipe that runs a suite) are named as unchecked, so a stale golden
    pin is never silent. Keys: ``stale``, ``unknown`` (the check did not finish),
    ``unchecked``; empty lists are omitted, ``{}`` when there is nothing to say."""
    try:
        recipes = load_recipes(repo, cfg)
    except ValueError as exc:  # an unreadable recipes file is not "nothing to say"
        return {"unknown": [str(exc)]}
    if not recipes:
        return {}
    try:
        rep = regenerate(repo, cfg, check=True, after_merge=True, recipes=recipes)
    except Exception as exc:  # a line of advice never stops a merge that has landed, or goes quiet
        return {"unknown": [f"the pin check failed: {type(exc).__name__}: {exc}"]}
    if rep.problem:
        return {"unknown": [rep.problem]}
    unchecked = [r.name for r in recipes if not r.after_merge]
    out = {
        "stale": [p.name for p in rep.stale],
        "unknown": [p.name for p in rep.unavailable],
        "unchecked": unchecked,
    }
    return {k: v for k, v in out.items() if v}


def merge_lines(report: dict[str, list[str]]) -> list[str]:
    """``merge_report`` as the lines `ddflow merge` prints, each with its command."""
    names = {k: ", ".join(v) for k, v in report.items()}
    lines = []
    if "stale" in report:
        lines.append(
            f"pins STALE after this merge: {names['stale']} -- `ddflow pins regenerate` "
            "re-records them (then commit them)"
        )
    if "unknown" in report:
        lines.append(
            f"pins not checked after this merge (the check did not finish): {names['unknown']}"
            " -- `ddflow pins regenerate --check`"
        )
    if "unchecked" in report:
        lines.append(
            f"pins the post-merge hook never re-records (they run a suite): {names['unchecked']}"
            " -- `ddflow pins regenerate --check` says whether this merge made them stale"
        )
    return lines


def _set_driver_config(repo: Path, line=None) -> list[str]:
    """Set the driver's `git config` keys where they are missing or differ; the actions.

    The keys go in the repository's own config, which a linked worktree shares with the
    primary checkout: setting them from any tree makes every tree use the driver."""
    driver = (line or command_line)("pins merge-driver", extra="%A %O %B")
    done: list[str] = []
    for key, val in (
        (f"merge.{DRIVER}.name", "ddflow pin file: merge the prose, regenerate after the merge"),
        (f"merge.{DRIVER}.driver", driver),
    ):
        cur = G.run(repo, "config", "--local", "--get", key)
        if cur.out.strip() != val:
            G.run(repo, "config", "--local", key, val)
            done.append(f"set git config {key}")
    return done


def _wanted(repo: Path, cfg: Config) -> list[str]:
    """The pin files a merge driver is meant to cover (none when it would not be safe)."""
    return [] if cfg.pins.after_merge == "off" else pin_files(repo, cfg)


#: Merge attributes that are no choice of a person's: unset, or git's own textual default.
_SOFT = ("unspecified", "text")
#: The name a file a directory pin might gain is asked about as (no tracked file has it).
_NEW = "/.ddflow-new-file"


def _driver_state(repo: Path) -> str:
    """ "ours" (a `pins merge-driver` command, from any launcher: the line falls back by itself),
    "foreign" (a command a person set; never overwritten here) or "missing"."""
    cur = G.run(repo, "config", "--get", f"merge.{DRIVER}.driver").out.strip()
    return "ours" if "pins merge-driver" in cur else ("foreign" if cur else "missing")


def _attr_line(repo: Path, f: str) -> str:
    pat = f.rstrip("/") + ("/**" if f.endswith("/") or (Path(repo) / f).is_dir() else "")
    return f'"{pat}" merge={DRIVER}' if " " in pat else f"{pat} merge={DRIVER}"


def _off(repo: Path, files: list[str]) -> dict[str, dict[str, str]]:
    """Per pin file, the paths git would NOT merge with the driver in this tree and the merge
    attribute each has: asked of git itself (`shared_files.drivers`), whatever `.gitattributes`,
    `info/attributes` or a worktree config say. A directory is asked about every tracked file in
    it, and about a file it might gain."""
    out: dict[str, dict[str, str]] = {}
    for f in files:
        base = f.rstrip("/")
        if f.endswith("/") or (Path(repo) / f).is_dir():
            now = {**SF.drivers(repo, base + "/**"), **SF.drivers(repo, base + _NEW)}
        else:
            now = SF.drivers(repo, base)
        bad = {q: v or "unspecified" for q, v in now.items() if v != DRIVER}
        if bad:
            out[f] = bad
    return out


def _lines_for(repo: Path, f: str, bad: dict[str, str]) -> list[str]:
    """The `info/attributes` lines that give the driver to what of ``f`` has no choice of a
    person's: the entry itself, or (a directory with a file another driver was given) each
    such file alone."""
    if all(v in _SOFT for v in bad.values()):
        return [_attr_line(repo, f)]
    return [_attr_line(repo, q) for q, v in bad.items() if v in _SOFT and q != f.rstrip("/") + _NEW]


def ensure_driver(repo: Path, cfg: Config) -> list[str]:
    """Make `git merge` in this tree use the driver (a claim calls it for the tree it binds).

    The git config goes in the repository's, which every linked worktree shares. The
    attributes of a tree that predates the tracked `.gitattributes` lines go in
    `info/attributes`, also shared and read before the tracked file: a merge reads the tree's
    attributes, not the incoming ones, so the first merge of main would otherwise conflict.
    A file a person gave another merge driver, and a driver command a person set, are left
    alone (`driver_gaps` says so). The actions."""
    files = _wanted(repo, cfg)
    if not files:
        return []
    acts = _set_driver_config(repo) if _driver_state(repo) == "missing" else []
    want = [x for f, bad in _off(repo, files).items() for x in _lines_for(repo, f, bad)]
    path = G.run(repo, "rev-parse", "--path-format=absolute", "--git-path", "info/attributes")
    if want and path.ok and path.out.strip():
        f = Path(path.out.strip())
        text = f.read_text("utf-8") if f.exists() else ""
        add = [a for a in want if a not in text.splitlines()]
        if add:
            f.parent.mkdir(parents=True, exist_ok=True)
            tail = "" if not text or text.endswith("\n") else "\n"
            replace_text(f, text + tail + "\n".join(add) + "\n")
            acts.append(f"added {len(add)} merge={DRIVER} line(s) to {f}")
    return acts


def driver_gaps(repo: Path, cfg: Config) -> list[str]:
    """What keeps `git merge` in THIS tree from using the driver on a pin file, as sentences."""
    files = _wanted(repo, cfg)
    if not files:
        return []
    gaps: list[str] = []
    state = _driver_state(repo)
    if state != "ours":
        gaps.append(
            f"git config merge.{DRIVER}.driver is "
            + (
                "not set"
                if state == "missing"
                else "another command than `ddflow pins merge-driver`"
            )
        )
    for f, bad in _off(repo, files).items():
        vals = sorted(set(bad.values()))
        gaps.append(f"no merge={DRIVER} attribute for {f} ({', '.join(vals)})")
    return gaps


def driver_notes(repo: Path, cfg: Config) -> list[str]:
    """`doctor`'s note when `git merge` would not use the driver on a pin file, with the remedy."""
    gaps = driver_gaps(repo, cfg)
    if not gaps:
        return []
    return [
        "pin files will conflict on a merge: " + "; ".join(gaps) + " -- `ddflow pins regenerate"
        " --install` sets the driver up (a claim sets a missing git config; `--install` also replaces another command)"
    ]


def install(repo: Path, cfg: Config, *, line=None) -> list[str]:
    """Write the merge driver for the recipes' files, and the post-merge hook; the actions."""
    line = line or command_line
    files = pin_files(repo, cfg)
    # Keeping our side is only safe while the post-merge hook re-records the other side's
    # pins: with after_merge off nothing would, so nothing is installed.
    if not files or cfg.pins.after_merge == "off":
        return []
    actions: list[str] = []
    attrs = Path(repo) / ".gitattributes"
    text = attrs.read_text("utf-8") if attrs.exists() else ""
    have = set(text.splitlines())
    add = [_attr_line(repo, f) for f in files]
    new = [a for a in add if a not in have]
    if new:
        body = text + ("" if not text or text.endswith("\n") else "\n") + "\n".join(new) + "\n"
        replace_text(attrs, body)
        actions += [f"added '{a}' to .gitattributes" for a in new]
    actions += _set_driver_config(repo, line)
    hook = hooks_dir(repo) / "post-merge"
    body = f"#!/bin/sh\n{HOOK_MARK}\n{line('pins regenerate --after-merge')}\n"
    if not hook.exists() or HOOK_MARK in hook.read_text("utf-8", errors="replace"):
        if not hook.exists() or hook.read_text("utf-8") != body:
            hook.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(hook, body)
            hook.chmod(0o755)
            actions.append(f"wrote {hook}")
    else:
        actions.append(f"{hook} is not ddflow's; add `ddflow pins regenerate --after-merge` to it")

    return actions
