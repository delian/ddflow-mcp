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

import contextlib
import os
import shlex
import sys
import threading
import time
import tomllib
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..infra import git as G
from ..infra import proc as P
from ..infra.fsio import LockTimeout, atomic_write, file_lock, replace_text
from .cmdrunner import TIMEOUT, CommandRunner, Declared
from .gateenv import REPO_VARS
from .pins_conflict import apply as apply_settled
from .pins_conflict import settle_files, take_side


@dataclass(frozen=True)
class Recipe:
    name: str
    files: tuple[str, ...]
    check: tuple[str, ...]
    regen: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()
    #: Whether the post-merge hook may run this recipe. A recipe that runs a test suite
    #: (golden snapshots) says false: a merge must never wait for it (Bf965d87b66).
    after_merge: bool = True


@dataclass
class PinState:
    name: str
    files: tuple[str, ...]
    stale: bool = False
    regenerated: bool = False
    error: str = ""
    #: The recipe could not finish (it timed out, or another run holds the tree): neither
    #: current nor stale nor failed -- unknown, and said so.
    unavailable: str = ""
    output: str = ""
    seconds: float = 0.0


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

    @property
    def unavailable(self) -> list[PinState]:
        return [p for p in self.pins if p.unavailable]


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
        later = t.get("after_merge", True)
        if not isinstance(later, bool):
            raise ValueError(
                f"{cfg.pins.recipes_file}: pin {name!r}: after_merge must be a boolean"
            )
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
                later,
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
    on_progress: Callable[[str], None] | None = None,
    after_merge: bool = False,
    bounded: bool = False,
) -> Report:
    """Check every pin (or `only`), and unless `check`, re-record the stale ones.

    ``after_merge`` is the post-merge hook's run: only the recipes that allow it, each command
    bounded by `[pins].after_merge_timeout_s`; one that cannot finish in time is
    ``unavailable``, never failed (a run asked for on the command line keeps `timeout_s` and
    reports it failed, as it always has). Two runs never overlap in one tree: the second is
    ``unavailable`` too, because a recipe re-records files the other is reading or writing
    (README.md left with thousands of duplicated lines, snapshots deleted: Bf965d87b66).

    ``bounded`` gives a run the same bound and the same soft timeout without restricting it to
    the hook's recipes: `ddflow merge`'s check (`[pins].merge_check`) runs every recipe it
    chooses that way, so a check that timed out or could not start is ``unavailable``."""
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
    if after_merge:
        chosen = [r for r in chosen if r.after_merge]
        if not chosen:
            return rep
    limit = cfg.pins.timeout_s
    if after_merge or bounded:
        limit = min(limit, cfg.pins.after_merge_timeout_s)
    # only the hook (and a bounded merge-time check) reports a timeout as unavailable; a run
    # asked for on the command line fails
    soft = after_merge or bounded
    held = contextlib.ExitStack()
    try:
        held.enter_context(_exclusive(repo, write=not check))
    except LockTimeout:
        why = "another pins regeneration is running in this tree; nothing was run"
        rep.pins.extend(PinState(r.name, r.files, unavailable=why) for r in chosen)
        return rep
    with held:
        return _regenerate_locked(repo, cfg, chosen, rep, check, tick, on_progress, limit, soft)


def _exclusive(repo: Path, *, write: bool):
    """The per-tree lock a re-recording holds; a check only reads and takes none."""
    git_dir = G.run(repo, "rev-parse", "--absolute-git-dir")
    if not write or not git_dir.ok or not git_dir.out.strip():
        return contextlib.nullcontext()
    return file_lock(Path(git_dir.out.strip()) / "ddflow-pins.lock", 0)


def _regenerate_locked(
    repo, cfg, chosen, rep, check, tick, on_progress, limit, soft=False
) -> Report:
    if not check and _unsettled(repo, chosen, rep, limit, tick):
        return rep
    shown = threading.Lock()  # the checks run on threads: one line is written at a time

    def say(line: str) -> None:
        if on_progress:
            with shown:
                on_progress(line)

    if tick is not None:  # the recipes' threads renew the caller's lease one at a time
        lock = threading.Lock()
        fn, every = tick

        def locked() -> None:
            with lock:
                fn()

        tick = (locked, every)
    states = _run_all(repo, cfg, chosen, check, tick, say, limit, soft)
    rep.pins.extend(states)
    return rep


def _pmap(fn, recipes: list[Recipe], workers: int, say, every: int, what: str) -> list:
    """``fn(recipe)`` for each recipe, ``workers`` at once, the answers in the recipes' order; a
    line naming the recipes still running every ``every`` seconds (0: none)."""
    out: dict[int, object] = {}
    running: dict[int, float] = {}
    guard = threading.Lock()

    def work(i: int) -> None:
        with guard:
            running[i] = time.monotonic()
        try:
            out[i] = fn(recipes[i])
        finally:
            with guard:
                running.pop(i, None)

    began = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(recipes)))) as pool:
        pending = {pool.submit(work, i) for i in range(len(recipes))}
        while pending:
            done, pending = wait(pending, timeout=every or None, return_when=FIRST_COMPLETED)
            for f in done:
                f.result()
            if pending and not done:
                with guard:
                    names = ", ".join(recipes[i].name for i in sorted(running))
                say(f"pins: {what} still running after {time.monotonic() - began:.0f}s: {names}")
    return [out[i] for i in range(len(recipes))]


def _run_all(
    repo, cfg, chosen: list[Recipe], check: bool, tick, say, limit, soft=False
) -> list[PinState]:
    """The checks at once (``[pins].parallel``: they only read), the re-recordings one after
    another (a suite refuses to run while another recipe changes the working tree), and the
    checks of what was re-recorded at once again."""
    states = {r.name: PinState(r.name, r.files) for r in chosen}

    def checked(r: Recipe) -> bool:
        say(f"pins: {r.name}: checking")
        began = time.monotonic()
        states[r.name].unavailable = ""  # what this check finds is what it finds
        c = _run(repo, r.check, r, limit, write=False, tick=tick)
        ok = c.error is None and c.returncode == 0
        st = states[r.name]
        st.stale, st.output = not ok, ("" if ok else _tail(c))
        st.seconds = time.monotonic() - began
        if c.error is not None and not c.timed_out and soft:  # the check never started
            st.stale, st.output = False, ""
            st.unavailable = f"the check could not start: {c.error}"
            say(f"pins: {r.name}: unavailable, {st.unavailable}")
            return False
        if c.timed_out and soft:  # not answered: neither current nor stale
            st.stale, st.output = False, ""
            st.unavailable = f"the check did not finish in {limit}s (timed out)"
            say(f"pins: {r.name}: unavailable, {st.unavailable}")
            return False
        say(f"pins: {r.name}: {'current' if ok else 'stale'} ({st.seconds:.0f}s)")
        return ok

    def again(r: Recipe) -> bool:
        st = states[r.name]
        was = (st.stale, st.output)  # why it was re-recorded: kept once it is current again
        ok = checked(r)
        if ok:
            st.stale, st.output = was
            st.regenerated = True
        elif st.unavailable:  # the re-check did not finish: nothing is known to be wrong
            st.stale, st.output = was
        else:
            st.error, st.unavailable = f"still stale after regeneration: {st.output}", ""
        return ok

    workers, every = cfg.pins.parallel, cfg.pins.progress_s
    _pmap(checked, chosen, workers, say, every, "checks")
    if check:
        return [states[r.name] for r in chosen]
    redone: list[Recipe] = []
    for r in (r for r in chosen if states[r.name].stale):
        say(f"pins: {r.name}: re-recording")
        began = time.monotonic()
        kept = _snapshot(repo, r)
        done = _run(repo, r.regen, r, limit, write=True, tick=tick)
        broke = done.error is not None or done.returncode != 0
        # A killed or failed regeneration leaves its files as it dropped them (README.md with
        # thousands of duplicated lines, snapshots deleted: Bf965d87b66): put them back.
        back = (
            " Its files were put back as they were."
            if broke and kept is not None and _restore(repo, r, kept)
            else (
                f" Its files may be half written: check `git status` for {', '.join(r.files)}."
                if broke
                else ""
            )
        )
        if done.timed_out and soft:
            states[
                r.name
            ].unavailable = f"the regeneration did not finish in {limit}s (timed out).{back}"
            say(f"pins: {r.name}: unavailable ({time.monotonic() - began:.0f}s)")
        elif broke:
            states[r.name].error = f"regeneration failed: {_tail(done)}{back}"
            states[r.name].unavailable = ""
            say(f"pins: {r.name}: FAILED ({time.monotonic() - began:.0f}s)")
        else:
            say(f"pins: {r.name}: re-recorded ({time.monotonic() - began:.0f}s)")
            redone.append(r)
    _pmap(again, redone, workers, say, every, "re-checks")
    return [states[r.name] for r in chosen]


def _owned(repo: Path, r: Recipe) -> list[Path]:
    """Every file the recipe owns: a directory in ``files`` is every file under it."""
    found: list[Path] = []
    for f in r.files:
        p = Path(repo) / f.rstrip("/")
        if p.is_file():
            found.append(p)
        elif p.is_dir():
            found.extend(sorted(q for q in p.rglob("*") if q.is_file()))
    return found


def _snapshot(
    repo: Path, r: Recipe, most: int = 64 * 1024 * 1024
) -> dict[Path, bytes | None] | None:
    """The recipe's files before it re-records them (a directory is a key with ``None``); None
    when they cannot be held (unreadable, or more than ``most`` bytes in all): then nothing is
    promised about putting them back."""
    kept: dict[Path, bytes | None] = {}
    total = 0
    try:
        for f in r.files:
            top = Path(repo) / f.rstrip("/")
            if top.is_dir():
                kept[top] = None
                kept.update({q: None for q in top.rglob("*") if q.is_dir()})
        for p in _owned(repo, r):
            data = p.read_bytes()
            total += len(data)
            if total > most:
                return None
            kept[p] = data
    except OSError:
        return None
    return kept


def _restore(repo: Path, r: Recipe, kept: dict[Path, bytes | None]) -> bool:
    """Put the recipe's files back as ``kept`` has them: rewritten when changed (through a
    symlink, which stays), recreated when deleted, removed when the run created them (a
    directory too). False if it could not."""
    try:
        for p in _owned(repo, r):
            if p not in kept:
                p.unlink()
        for f in r.files:
            top = Path(repo) / f.rstrip("/")
            if top.is_dir():
                for d in sorted((q for q in top.rglob("*") if q.is_dir()), reverse=True):
                    if d not in kept and not any(d.iterdir()):
                        d.rmdir()
        for p, data in kept.items():
            if data is None:
                p.mkdir(parents=True, exist_ok=True)
            elif not p.is_file() or p.read_bytes() != data:
                p.parent.mkdir(parents=True, exist_ok=True)
                where = Path(os.path.realpath(p)) if p.is_symlink() else p  # the link stays
                if _is_text(data):
                    replace_text(where, data.decode("utf-8"))
                else:
                    atomic_write(where, data)
    except OSError:
        return False
    return True


def _is_text(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _owns(r: Recipe, path: str) -> bool:
    return any(path == f.rstrip("/") or path.startswith(f.rstrip("/") + "/") for f in r.files)


def _decided_by_generators(
    repo: Path, s, chosen: list[Recipe], limit: int, tick, torn: list[str] | None = None
) -> bool:
    """Whether the recipes that own ``s``'s file settle its prose conflicts: the file is taken
    our way and then theirs, each re-recorded by its owners, and kept only when both come out
    the same (a knob count a generator writes, say). Otherwise it is put back as it was; a
    recipe whose files could not all be put back is named in ``torn``."""
    owners = [r for r in chosen if s.file is not None and _owns(r, s.path)]
    if not owners or s.file is None:
        return False
    original = s.file.read_text("utf-8")
    result: dict[str, str] = {}
    snaps = [(r, _snapshot(repo, r)) for r in owners]

    def put_back() -> None:  # the file as it was, and whatever else a killed regeneration touched
        replace_text(s.file, original)
        for r, kept in snaps:
            if kept is not None and not _restore(repo, r, kept) and torn is not None:
                torn.append(r.name)

    for side in ("ours", "theirs"):
        replace_text(s.file, take_side(original, side))
        for r in owners:
            if _run(repo, r.check, r, limit, write=False, tick=tick).returncode == 0:
                continue
            if _run(repo, r.regen, r, limit, write=True, tick=tick).returncode != 0:
                put_back()
                return False
        result[side] = s.file.read_text("utf-8")
    if result["ours"] == result["theirs"]:
        replace_text(s.file, result["ours"])
        return True
    put_back()
    return False


def _unsettled(repo: Path, chosen: list[Recipe], rep: Report, limit: int = 900, tick=None) -> bool:
    """Settle the conflict markers a merge left in the pin files (`pins_conflict`): True, with
    ``rep.problem`` saying where, when one cannot be settled -- markers that do not pair up, or
    a conflict in hand-written prose that the generators do not decide the same either way."""
    files = sorted({f.rstrip("/") for r in chosen for f in r.files})
    found = settle_files(repo, files)
    stuck = []
    torn: list[str] = []
    for s in found:
        if s.refused and not s.stray and _decided_by_generators(repo, s, chosen, limit, tick, torn):
            rep.resolved.append(
                f"{s.path}: {s.hunks} conflict(s) in generated content or in text the "
                "generators write, re-recorded"
            )
        elif s.refused:
            stuck.append(s)
    if stuck:
        rep.problem = "; ".join(
            f"{s.path}: "
            + (
                "conflict markers that do not pair up"
                if not s.hunks or s.stray
                else "conflict in prose outside the generated regions"
            )
            + f" (line {', '.join(map(str, s.refused))}): resolve it by hand, then run this again"
            for s in stuck
        )
        if torn:
            names = ", ".join(sorted(set(torn)))
            rep.problem += (
                f". The files of {names} could not all be put back after a failed regeneration "
                "and may be half written: check `git status`"
            )
        return True
    apply_settled([s for s in found if not s.refused])
    rep.resolved += [
        f"{s.path}: {s.hunks} conflict(s) in generated content, our side taken and re-recorded"
        for s in found
        if not s.refused
    ]
    return False


def pin_files(repo: Path, cfg: Config) -> list[str]:
    try:
        return sorted({f for r in load_recipes(repo, cfg) for f in r.files})
    except ValueError:
        return []
