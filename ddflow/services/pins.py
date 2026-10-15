"""Pin files regenerate themselves (DX-pins-regenerate, D-lock-cycle-selfheal).

A pin file is a recorded expectation -- a golden snapshot, a CLI transcript, a generated
README table. Every knob, command or event kind changes the same few, so hand-merging or
leasing them serialised a whole parallel wave. They are regenerated instead, by the recipe
each kind carries. Recipes are DATA, in the file `[pins].recipes_file` names:

    [[pin]]
    name = "snapshots"
    files = ["tests/golden/__snapshots__"]
    check = ["{python}", "-m", "pytest", "tests/golden", "-q", "-p", "no:xdist"]
    regen = ["{python}", "-m", "pytest", "tests/golden", "-q", "-p", "no:xdist", "--snapshot-update"]

`check` exits 0 when the pin is current; `regen` re-records only what differs. A new kind
of pin is a new table. `regenerate` runs `check` for each pin, `regen` for each that is
stale, then `check` again. It refuses to run inside an xdist worker: a snapshot update is
not safe in parallel.

`merge_driver` (a no-op that keeps our side) plus the post-merge hook `install` writes make
two branches that each changed a pin merge into green pins with no manual step.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..infra import git as G
from ..infra import proc as P
from ..infra.fsio import atomic_write, replace_text

#: The merge driver's name in `.gitattributes` and `git config merge.<name>.driver`.
DRIVER = "pinsregen"
HOOK_MARK = "# DDFLOW-PINS-HOOK v1 -- managed by `ddflow pins regenerate --install`"


@dataclass(frozen=True)
class Recipe:
    name: str
    files: tuple[str, ...]
    check: tuple[str, ...]
    regen: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()


@dataclass
class PinState:
    name: str
    files: tuple[str, ...]
    stale: bool = False
    regenerated: bool = False
    error: str = ""
    output: str = ""


@dataclass
class Report:
    pins: list[PinState] = field(default_factory=list)
    problem: str = ""

    @property
    def stale(self) -> list[PinState]:
        return [p for p in self.pins if p.stale]

    @property
    def failed(self) -> list[PinState]:
        return [p for p in self.pins if p.error]


def _argv(raw: object, label: str) -> tuple[str, ...]:
    if not (isinstance(raw, list) and raw and all(isinstance(a, str) for a in raw)):
        raise ValueError(f"{label} must be a non-empty list of strings")
    return tuple(sys.executable if a == "{python}" else a for a in raw)


def load_recipes(repo: Path, cfg: Config) -> list[Recipe]:
    """The project's recipes; ValueError names what is wrong with the file."""
    path = Path(repo) / cfg.pins.recipes_file
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{cfg.pins.recipes_file}: {exc}") from exc
    out: list[Recipe] = []
    for i, t in enumerate(data.get("pin", [])):
        name = t.get("name") if isinstance(t, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError(f"{cfg.pins.recipes_file}: pin #{i + 1} has no name")
        if any(r.name == name for r in out):
            raise ValueError(f"{cfg.pins.recipes_file}: pin {name!r} is declared twice")
        files = t.get("files", [])
        if not (isinstance(files, list) and all(isinstance(f, str) for f in files)):
            raise ValueError(f"{cfg.pins.recipes_file}: pin {name!r}: files must be strings")
        env = t.get("env", {})
        out.append(
            Recipe(
                name,
                tuple(files),
                _argv(t.get("check"), f"{name}: check"),
                _argv(t.get("regen"), f"{name}: regen"),
                tuple((str(k), str(v)) for k, v in env.items()),
            )
        )
    return out


def _run(repo: Path, argv: tuple[str, ...], r: Recipe, timeout: int) -> P.Captured:
    env = {**os.environ, **dict(r.env)}
    env.pop("PYTEST_XDIST_WORKER", None)
    return P.capture(list(argv), timeout=timeout, cwd=repo, env=env)


def _tail(c: P.Captured) -> str:
    text = (c.stdout + c.stderr).strip()
    return text[-1500:] if c.error is None else f"{c.error}"


def regenerate(
    repo: Path,
    cfg: Config,
    *,
    only: str = "",
    check: bool = False,
    recipes: list[Recipe] | None = None,
) -> Report:
    """Check every pin (or `only`), and unless `check`, re-record the stale ones."""
    rep = Report()
    if os.environ.get("PYTEST_XDIST_WORKER"):
        rep.problem = "pins regenerate must not run under xdist: a snapshot update is not parallel-safe"
        return rep
    try:
        chosen = recipes if recipes is not None else load_recipes(repo, cfg)
    except ValueError as exc:
        rep.problem = str(exc)
        return rep
    if only:
        chosen = [r for r in chosen if r.name == only]
        if not chosen:
            rep.problem = f"no pin named {only!r} in {cfg.pins.recipes_file}"
            return rep
    if not chosen:
        rep.problem = f"no pin recipes: {cfg.pins.recipes_file} is missing or has no [[pin]]"
        return rep
    limit = cfg.pins.timeout_s
    for r in chosen:
        st = PinState(r.name, r.files)
        rep.pins.append(st)
        first = _run(repo, r.check, r, limit)
        if first.error is None and first.returncode == 0:
            continue
        st.stale = True
        st.output = _tail(first)
        if check:
            continue
        done = _run(repo, r.regen, r, limit)
        if done.error is not None or done.returncode != 0:
            st.error = f"regeneration failed: {_tail(done)}"
            continue
        again = _run(repo, r.check, r, limit)
        if again.error is not None or again.returncode != 0:
            st.error = f"still stale after regeneration: {_tail(again)}"
            continue
        st.regenerated = True
    return rep


def merge_driver(ours: str) -> int:
    """`git merge` driver for a pin file: keep our side (the file at `ours` is the result) so
    the merge does not stop; the post-merge hook then re-records every stale pin."""
    return 0 if Path(ours).exists() else 1


def pin_files(repo: Path, cfg: Config) -> list[str]:
    try:
        return sorted({f for r in load_recipes(repo, cfg) for f in r.files})
    except ValueError:
        return []


def after_merge(repo: Path, cfg: Config) -> str:
    """Post-merge: regenerate; fold the result into the merge commit when only pin files
    changed. Returns one line saying what happened."""
    mode = cfg.pins.after_merge
    if mode == "off":
        return ""
    rep = regenerate(repo, cfg)
    if rep.problem or not rep.pins:
        return ""
    if rep.failed:
        return "pins: " + "; ".join(f"{p.name}: {p.error}" for p in rep.failed)
    done = [p.name for p in rep.pins if p.regenerated]
    if not done:
        return ""
    files = pin_files(repo, cfg)
    paths = G.run(repo, "diff", "--name-only", "-z", "HEAD", z=True).paths() or []
    own = tuple(f.rstrip("/") for f in files)
    merge = G.run(repo, "rev-parse", "-q", "--verify", "HEAD^2").ok
    if mode == "amend" and merge and paths and all(p.startswith(own) for p in paths):
        # `git commit --amend` is refused while MERGE_HEAD still exists, which it does in
        # this hook: write the amended commit with plumbing and move the branch to it.
        added = G.run(repo, "add", "-A", "--", *files)
        tree = G.run(repo, "write-tree")
        head = G.run(repo, "rev-parse", "HEAD")
        parents = G.run(repo, "rev-list", "--parents", "-n", "1", "HEAD").out.split()[1:]
        msg = G.run(repo, "log", "-1", "--format=%B", "HEAD")
        args = ["commit-tree", tree.out.strip(), *[x for p in parents for x in ("-p", p)]]
        new = G.run(repo, *args, input=msg.out + "\n") if all(
            r.ok for r in (added, tree, head, msg)
        ) else added
        if new.ok and G.run(repo, "update-ref", "HEAD", new.out.strip(), head.out.strip()).ok:
            return f"pins regenerated into the merge commit: {', '.join(done)}"
        return f"pins regenerated, not committed ({new.err}): {', '.join(done)}"
    return f"pins regenerated, not committed: {', '.join(done)}; commit them"


def install(repo: Path, cfg: Config, *, line=None) -> list[str]:
    """Write the merge driver for the recipes' files, and the post-merge hook; the actions."""
    from .enforce import command_line, hooks_dir

    line = line or command_line
    files = pin_files(repo, cfg)
    if not files:
        return []
    actions: list[str] = []
    attrs = Path(repo) / ".gitattributes"
    text = attrs.read_text("utf-8") if attrs.exists() else ""
    have = set(text.splitlines())
    add = [
        f"{f.rstrip('/')}{'/**' if f.endswith('/') or (Path(repo) / f).is_dir() else ''} merge={DRIVER}"
        for f in files
    ]
    new = [a for a in add if a not in have]
    if new:
        body = text + ("" if not text or text.endswith("\n") else "\n") + "\n".join(new) + "\n"
        replace_text(attrs, body)
        actions += [f"added '{a}' to .gitattributes" for a in new]
    driver = line("pins merge-driver", extra="%A")
    for key, val in (
        (f"merge.{DRIVER}.name", "ddflow pin file: keep ours, regenerate after the merge"),
        (f"merge.{DRIVER}.driver", driver),
    ):
        cur = G.run(repo, "config", "--local", "--get", key)
        if cur.out.strip() != val:
            G.run(repo, "config", "--local", key, val)
            actions.append(f"set git config {key}")
    if cfg.pins.after_merge != "off":
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
