#!/usr/bin/env python3
"""The release's bump level, from the impact the upgrade manifest declares (D-compat).

A release used to be numbered by "patch, always". docs/ddflow/compatibility.md says it is
numbered by the IMPACT of its changes: only fixes, additive and deprecating changes are a
patch; a breaking change, while ddflow is 0.x, is a minor. The impact is declared per entry in
`ddflow/templates/upgrade/` (`impact = "breaking"`), as a fragment under `unreleased/` or
inside a release block. This is the one place that turns those declarations into a number:

    release_impact.py base                      the git ref of the last release
    release_impact.py level [--base REF]        patch | minor, from what the manifest gained
                                                since REF (default: the last release)
    release_impact.py next VERSION LEVEL        the version a release at LEVEL moves VERSION to
    release_impact.py candidate                 the version a release made now would be numbered
    release_impact.py check [--base REF]        exit 1 when the declared __version__ is a smaller
                                                step than the declared impact asks for

"Gained since REF" is a comparison of the manifest at REF with the manifest now, ignoring the
release an entry sits in: cutting a version moves fragments into a release block and must not
make the same breaking entry count a second time (or never). The last release is the newest
of the latest `release X.Y.Z` commit the publish workflow makes and the latest `v*` tag.

Stdlib only for `base` and `next` (`scripts/bump.sh` calls `next` with the system python); the
manifest comparisons import `ddflow.services.upgrade_manifest`. It runs in CI before anything
is built.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ddflow.services.upgrade_manifest import Change, Manifest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MANIFEST_DIR = "ddflow/templates/upgrade"
#: What a project with no manifest (yet) has declared: nothing.
EMPTY_MANIFEST = 'schema_version = 1\n\n[base]\nversion = "0.0.0"\n'
PATCH, MINOR, MAJOR = "patch", "minor", "major"
_RELEASE_SUBJECT = re.compile(r"^release (\d+\.\d+\.\d+)$")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _um():
    from ddflow.services import upgrade_manifest

    return upgrade_manifest


def _vkey(version: str) -> tuple[int, ...]:
    m = _VERSION.match(version)
    return tuple(int(x) for x in m.groups()) if m else ()


def _git(*argv: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *argv], capture_output=True, text=True, cwd=cwd or ROOT)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(argv)}: {r.stderr.strip() or r.returncode}")
    return r.stdout


def _newest(found: list[tuple[str, str]], cwd: Path | None = None) -> tuple[str, str] | None:
    """Of ``(ref, version)`` pairs, the one latest in history (the others are its ancestors)."""
    best: tuple[str, str] | None = None
    for cand in found:
        if best is None:
            best = cand
            continue
        # cand is later than best when best is its ancestor
        if (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", best[0], cand[0]], cwd=cwd or ROOT
            ).returncode
            == 0
        ):
            best = cand
    return best


def version_commit(version: str, cwd: Path | None = None) -> str | None:
    """The commit that last made ``version`` the declared `__version__` (the newest commit
    that added the line), or None when no commit did."""
    out = _git(
        "log",
        "-1",
        "--format=%H",
        f'-S__version__ = "{version}"',
        "--",
        "ddflow/__init__.py",
        cwd=cwd,
    ).strip()
    return out or None


def last_release(cwd: Path | None = None, *, published: str = "") -> tuple[str, str] | None:
    """``(git ref, version)`` of the most recent release reachable from HEAD, or None when
    there has not been one: the newest of the latest `release X.Y.Z` commit, the latest `v*`
    tag and, when the caller knows ``published`` (a version PyPI has) is declared, the commit
    that declared it -- a release a person made by hand and CI published as declared leaves
    no `release` commit behind."""
    found: list[tuple[str, str]] = []
    log = _git("log", "-E", "--grep=^release [0-9]+\\.[0-9]+\\.[0-9]+$", "--format=%H %s", cwd=cwd)
    for line in log.splitlines():
        sha, _, subject = line.partition(" ")
        m = _RELEASE_SUBJECT.match(subject)
        if m:
            found.append((sha, m.group(1)))
            break
    try:
        tag = _git(
            "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD", cwd=cwd
        ).strip()
    except RuntimeError:  # no tag is reachable from HEAD
        tag = ""
    if _VERSION.match(tag[1:]):
        found.append((tag, tag[1:]))
    if published and (commit := version_commit(published, cwd)):
        found.append((commit, published))
    return _newest(found, cwd)


def manifest_at(ref: str | None, cwd: Path | None = None) -> Manifest:
    """The upgrade manifest, with its unreleased fragments, as it was at ``ref`` (the working
    tree when ``ref`` is None)."""
    where = cwd or ROOT
    UM = _um()
    if ref is None:
        path = where / MANIFEST_DIR / "changes.toml"
        return UM.load(path) if path.is_file() else UM.parse(EMPTY_MANIFEST)
    try:
        text = _git("show", f"{ref}:{MANIFEST_DIR}/changes.toml", cwd=cwd)
    except RuntimeError:  # the manifest did not exist yet at that ref: nothing was declared
        return UM.parse(EMPTY_MANIFEST)
    names = _git("ls-tree", "--name-only", ref, f"{MANIFEST_DIR}/unreleased/", cwd=cwd).split()
    frags = [
        (Path(n).name, _git("show", f"{ref}:{n}", cwd=cwd)) for n in names if n.endswith(".toml")
    ]
    return UM.parse(text, frags)


def _identity(c: Change) -> tuple:
    """What an entry IS, apart from the release block it sits in."""
    return (
        c.kind,
        c.key,
        json.dumps(c.old),
        json.dumps(c.new),
        c.impact,
        c.why,
        c.effect,
        c.enable,
    )


def gained(base: Manifest, now: Manifest) -> list[Change]:
    """The entries ``now`` has that ``base`` did not, wherever they sit."""
    before = {_identity(c) for c in base.changes()}
    return [c for c in now.changes() if _identity(c) not in before]


def level_of(changes: list[Change], version: str) -> str:
    """``minor`` when any change is declared breaking and ``version`` is a 0.x release (the
    contract's rule), else ``patch``."""
    breaking = any(c.impact == "breaking" for c in changes)
    m = _VERSION.match(version)
    return MINOR if breaking and m and int(m.group(1)) == 0 else PATCH


def next_version(version: str, level: str) -> str:
    m = _VERSION.match(version)
    if not m:
        raise ValueError(f"{version!r} is not X.Y.Z")
    a, b, c = (int(x) for x in m.groups())
    if level == MAJOR:
        return f"{a + 1}.0.0"
    if level == MINOR:
        return f"{a}.{b + 1}.0"
    if level == PATCH:
        return f"{a}.{b}.{c + 1}"
    raise ValueError(f"unknown level {level!r}: patch, minor or major")


def level_since(ref: str | None = None, cwd: Path | None = None) -> str:
    """The level the manifest's gains since ``ref`` (default: the last release) call for; a
    project with no release yet is a patch."""
    if ref is None:
        last = last_release(cwd)
        if last is None:
            return PATCH
        ref, version = last
    else:
        version = _release_version(ref, cwd)
    return level_of(gained(manifest_at(ref, cwd), manifest_at(None, cwd)), version)


def _release_version(ref: str, cwd: Path | None = None) -> str:
    """The version released at ``ref``: from a `v*` tag's name or a release commit's subject,
    else the version `ddflow/__init__.py` declared there."""
    if _VERSION.match(ref.lstrip("v")) and ref.startswith("v"):
        return ref[1:]
    subject = _git("log", "-1", "--format=%s", ref, cwd=cwd).strip()
    if m := _RELEASE_SUBJECT.match(subject):
        return m.group(1)
    declared = re.search(
        r'^__version__ = "(.*)"$', _git("show", f"{ref}:ddflow/__init__.py", cwd=cwd), re.M
    )
    return declared.group(1) if declared else "0.0.0"


def declared_version(cwd: Path | None = None) -> str:
    text = ((cwd or ROOT) / "ddflow" / "__init__.py").read_text("utf-8")
    m = re.search(r'^__version__ = "(.*)"$', text, re.M)
    return m.group(1) if m else ""


def candidate(cwd: Path | None = None) -> tuple[str, str]:
    """``(version, level)`` a release made now would be numbered, for a declared version that
    is ALREADY PUBLISHED (the bump path: PyPI has it). The step is the declared impact's, taken
    from the release that impact was measured against -- the newest of the last `release`
    commit, the last `v*` tag (published out of band; it never moves main's declared version)
    and the commit that declared the published version (a hand release leaves no `release`
    commit)."""
    base = last_release(cwd, published=declared_version(cwd))
    if base is None:
        return next_version(declared_version(cwd), PATCH), PATCH
    level = level_since(base[0], cwd)
    return next_version(base[1], level), level


def check(ref: str | None = None, cwd: Path | None = None) -> tuple[bool, str]:
    """Is the declared ``__version__`` a big enough step from the last release for the impact
    the manifest declares? ``(ok, a sentence)``."""
    last = (ref, _release_version(ref, cwd)) if ref else last_release(cwd)
    if last is None:
        return True, "no earlier release: nothing to compare"
    ref, released = last
    level = level_since(ref, cwd)
    declared = declared_version(cwd)
    if level == PATCH:
        return True, (
            f"{declared} after {released}: no breaking change is declared since, so any "
            f"version is enough (a declared version PyPI does not have is published as it is)"
        )
    if _vkey(declared) >= _vkey(next_version(released, MINOR)):
        return True, f"{declared} after {released}: a minor step, as the declared impact asks"
    return False, (
        f"the manifest declares a BREAKING change since {released}, which is a minor release "
        f"while ddflow is 0.x ({next_version(released, MINOR)}), but ddflow/__init__.py "
        f"declares {declared}. Run `scripts/bump.sh minor` and commit, or correct the "
        f"`impact` of the entry if it is not breaking (docs/ddflow/compatibility.md)"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release_impact.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("base", help="the git ref of the last release (exit 2: there is none)")
    for name, text in (
        ("level", "patch | minor, from what the manifest gained since the base"),
        ("check", "exit 1 when the declared version is a smaller step than the impact asks"),
    ):
        sp = sub.add_parser(name, help=text)
        sp.add_argument(
            "--base", default=None, help="the ref to compare with (default: last release)"
        )
    sub.add_parser("candidate", help="the version a release made now would be numbered")
    nxt = sub.add_parser("next", help="the version a release at LEVEL moves VERSION to")
    nxt.add_argument("version")
    nxt.add_argument("level")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse's own exit 2 is "bad usage"; keep it as such
        return int(exc.code or 0)
    try:
        if args.cmd == "base":
            last = last_release()
            if last is None:
                return 2
            print(last[0])
        elif args.cmd == "level":
            print(level_since(args.base))
        elif args.cmd == "candidate":
            version, level = candidate()
            print(
                f"declared impact since the last release asks for a {level} bump", file=sys.stderr
            )
            print(version)
        elif args.cmd == "next":
            print(next_version(args.version, args.level))
        else:
            ok, why = check(args.base)
            print(why, file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1
    except (RuntimeError, ValueError) as exc:  # a ManifestError is a ValueError
        print(f"release_impact: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
