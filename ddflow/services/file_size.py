"""Python files over `[quality].max_file_bytes` (r-small-files), and the shrink-only baseline.

A lease covers a file, so one big file serialises every agent that needs any part of it, and
a small-context model cannot read it. The ratchet (`tests/test_file_size_ratchet.py`) and the
`ddflow doctor` note both answer from `oversized`. The baseline lists today's exceptions, one
per line as ``path | bytes | reason``; a split removes its own line and nothing adds one.
"""

from __future__ import annotations

from pathlib import Path

from ..infra import git as G

#: The baseline of exempt files, relative to the repository root. It lives outside
#: `tests/guard_baselines/` (that directory is reserved for the architecture guards,
#: which assert nothing else is there).
BASELINE = "tests/file_size_baseline.txt"

#: Files named in the doctor note before the rest are counted.
SHOWN = 5


def _listed(repo: Path) -> list[str]:
    """The `.py` files, or raise: a listing git could not give is not 'no files'."""
    listed = G.files(repo, pathspec=("*.py",))
    if listed is None:
        raise RuntimeError(f"git could not list the files of {repo}: cannot measure file sizes")
    return listed


def oversized(repo: Path, limit: int) -> dict[str, int]:
    """Tracked or new, not git-ignored, `.py` files over ``limit`` bytes, by path.

    {} when ``limit`` is 0 (the check is off); RuntimeError when git could not list the files."""
    if limit <= 0:
        return {}
    out: dict[str, int] = {}
    for rel in _listed(repo):
        try:
            size = (repo / rel).stat().st_size
        except OSError:  # listed but deleted in the work tree
            continue
        if size > limit:
            out[rel] = size
    return out


def read_baseline(repo: Path) -> dict[str, tuple[int, str]]:
    """{path: (recorded bytes, reason)}; blank lines and ``#`` comments are skipped."""
    path = repo / BASELINE
    if not path.is_file():
        return {}
    out: dict[str, tuple[int, str]] = {}
    for line in path.read_text("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, size, reason = (p.strip() for p in line.split("|", 2))
        out[name] = (int(size), reason)
    return out


def violations(repo: Path, limit: int) -> list[str]:
    """What breaks the ratchet: a new oversized file, a baselined file that grew, a baseline
    line that is stale (the file is gone or now under the limit: remove the line)."""
    if limit <= 0:  # the check is off: no file is over a limit that is not set
        return []
    over, base = oversized(repo, limit), read_baseline(repo)
    bad = []
    for rel, size in sorted(over.items()):
        if rel not in base:
            bad.append(f"{rel} is {size} bytes, over the {limit} limit: split it into a package")
        elif size > base[rel][0]:
            bad.append(f"{rel} grew to {size} bytes (baseline {base[rel][0]}): split it")
    for rel in sorted(set(base) - set(over)):
        recorded, reason = base[rel]
        bad.append(
            f"{rel} is no longer over {limit} bytes: delete its line from {BASELINE}: "
            f"`{rel} | {recorded} | {reason}`"
        )
    return bad


def _base_size(repo: Path, base: str, rel: str) -> int | None:
    """Bytes of ``rel`` at ``base``; None when the base does not have the file."""
    r = G.run(repo, "cat-file", "-s", f"{base}:{rel}")
    return int(r.out.strip()) if r.ok and r.out.strip().isdigit() else None


def growth(repo: Path, changed: list[str], limit: int, base: str) -> list[str]:
    """What a change adds to the size problem, for the files it touches (r-small-files).

    A changed ``.py`` file that is over ``limit`` now is refused when the base did not have
    it over the limit (a new oversized file, or one that crossed it), and when it was over
    already and is BIGGER than on the base. A shrink, an untouched offender and a file under
    the limit pass. The baseline never licenses growth: it records today's exceptions, and a
    ratchet that lets an exempt file grow is not one. The limit and the baseline are only
    read. Sizes are bytes on disk, as `oversized` measures them (so a checkout that rewrites line
    endings is measured as it sits). RuntimeError when ``base`` is not a commit the repository can read: a check that
    could not run is never a pass. ``limit`` 0 turns it off."""
    if limit <= 0:
        return []
    if not G.run(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").ok:
        raise RuntimeError(f"cannot measure growth: git cannot read the base {base!r}")
    bad = []
    for rel in sorted(set(changed)):
        if not rel.endswith(".py"):
            continue
        try:
            size = (repo / rel).stat().st_size
        except FileNotFoundError:
            if not (repo / rel).is_symlink():  # deleted (a path that is gone is no growth)
                continue
            raise RuntimeError(f"cannot measure {rel}: a dangling link") from None
        except OSError as exc:  # unreadable is not "deleted": the check did not see the bytes
            raise RuntimeError(f"cannot measure {rel}: {exc.strerror or exc}") from exc
        if size <= limit:
            continue
        before = _base_size(repo, base, rel)
        if before is None or before <= limit:
            bad.append(f"{rel} is {size} bytes, over the {limit} limit: split it into a package")
        elif size > before:
            bad.append(f"{rel} grew to {size} bytes (on {base}: {before}): split it")
    return bad


def doctor_notes(repo: Path, limit: int) -> list[str]:
    """One note listing the files over the limit (largest first); [] when there are none."""
    try:
        over = oversized(repo, limit)
    except RuntimeError:  # advisory: git unable to answer is silence, never a claim
        return []
    if not over:
        return []
    ranked = sorted(over.items(), key=lambda kv: -kv[1])
    shown = ", ".join(f"{p} ({n} B)" for p, n in ranked[:SHOWN])
    more = f" and {len(ranked) - SHOWN} more" if len(ranked) > SHOWN else ""
    return [
        f"{len(ranked)} Python file(s) over [quality].max_file_bytes={limit}: {shown}{more}; "
        "split each into a package (r-small-files); list: tests/test_file_size_ratchet.py"
    ]
