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

`services.pins_merge` holds the git side: the merge driver and the post-merge hook that make
two branches that each changed a pin merge into green pins with no manual step.
"""

from __future__ import annotations

import os
import shlex
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..infra import proc as P
from .cmdrunner import TIMEOUT, CommandRunner, Declared
from .gateenv import REPO_VARS
from .pins_conflict import apply as apply_settled
from .pins_conflict import settle_files


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
    resolved: list[str] = field(default_factory=list)  # conflicts settled before regenerating

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
        if not isinstance(env, dict):
            raise ValueError(f"{cfg.pins.recipes_file}: pin {name!r}: env must be a table")
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


#: (a callback, its interval in seconds): called while a recipe command runs.
Tick = tuple[Callable[[], None], float]


def _run(
    repo: Path,
    argv: tuple[str, ...],
    r: Recipe,
    timeout: int,
    *,
    write: bool,
    tick: Tick | None = None,
) -> P.Captured:
    """One recipe command; the recipe's ``env`` reaches only ``regen``, so a check can never
    re-record what it is checking."""
    # Not the caller's git repository: run by the post-merge hook this inherits GIT_DIR and
    # GIT_INDEX_FILE, and the tests it runs then commit "init" to the worktree (B683c75d7b3).
    env = {k: v for k, v in os.environ.items() if k not in REPO_VARS}
    env.update(dict(r.env) if write else {})
    if not write:
        env.pop("DDFLOW_UPDATE_GOLDEN", None)
    env.pop("PYTEST_XDIST_WORKER", None)
    if tick is None:
        return P.capture(list(argv), timeout=timeout, cwd=repo, env=env)
    # A long recipe command renews its caller's lease between polls, on this thread.
    res = CommandRunner().run(
        Declared(shlex.join(argv), f"{r.name} in [pins].recipes_file"),
        cwd=repo,
        env=env,
        timeout_s=timeout,
        on_tick=tick[0],
        tick_s=tick[1],
    )
    if res.kind == TIMEOUT:
        return P.Captured(error=P.TimeoutExpired(list(argv), timeout))
    if not res.ran:
        return P.Captured(error=OSError(res.reason))
    return P.Captured(res.code, res.out, res.err)


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
    tick: Tick | None = None,
) -> Report:
    """Check every pin (or `only`), and unless `check`, re-record the stale ones."""
    rep = Report()
    if os.environ.get("PYTEST_XDIST_WORKER"):
        rep.problem = (
            "pins regenerate must not run under xdist: a snapshot update is not parallel-safe"
        )
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
    if not check and _unsettled(repo, chosen, rep):
        return rep
    limit = cfg.pins.timeout_s
    for r in chosen:
        st = PinState(r.name, r.files)
        rep.pins.append(st)
        first = _run(repo, r.check, r, limit, write=False, tick=tick)
        if first.error is None and first.returncode == 0:
            continue
        st.stale = True
        st.output = _tail(first)
        if check:
            continue
        done = _run(repo, r.regen, r, limit, write=True, tick=tick)
        if done.error is not None or done.returncode != 0:
            st.error = f"regeneration failed: {_tail(done)}"
            continue
        again = _run(repo, r.check, r, limit, write=False, tick=tick)
        if again.error is not None or again.returncode != 0:
            st.error = f"still stale after regeneration: {_tail(again)}"
            continue
        st.regenerated = True
    return rep


def _unsettled(repo: Path, chosen: list[Recipe], rep: Report) -> bool:
    """Settle the conflict markers a merge left in the pin files (`pins_conflict`): True, with
    ``rep.problem`` saying where, when one is in hand-written prose and nothing was changed."""
    files = sorted({f.rstrip("/") for r in chosen for f in r.files})
    found = settle_files(repo, files)
    stuck = [s for s in found if s.refused]
    if stuck:
        rep.problem = "; ".join(
            f"{s.path}: conflict in prose outside the generated regions (line "
            f"{', '.join(map(str, s.refused))}): resolve it by hand, then run this again"
            for s in stuck
        )
        return True
    apply_settled(found)
    rep.resolved = [
        f"{s.path}: {s.hunks} conflict(s) in generated content, our side taken and re-recorded"
        for s in found
    ]
    return False


def pin_files(repo: Path, cfg: Config) -> list[str]:
    try:
        return sorted({f for r in load_recipes(repo, cfg) for f in r.files})
    except ValueError:
        return []
