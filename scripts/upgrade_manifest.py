#!/usr/bin/env python3
"""Build and inspect the shipped upgrade manifest (ddflow/templates/upgrade/changes.toml).

    scripts/upgrade_manifest.py snapshot [--tree DIR]     knobs + event kinds of a tree, as JSON
    scripts/upgrade_manifest.py backfill [--base 0.1.3]   rebuild the manifest from release commits

`backfill` finds every release in git history -- the first commit at which
`ddflow/__init__.py` declares each new `__version__` -- extracts that commit's `ddflow/`
package with `git archive`, imports it in a subprocess, and records its config knob
defaults and event kinds. The oldest release at or after `--base` becomes the manifest's
base; each later release lists what changed from the one before; and the working tree, if
it differs from the newest release, becomes the `unreleased` entry. Entries already in the
manifest keep their hand-written `why`, `effect`, `impact` and `enable`, and refresh,
repair and feature entries (which no snapshot can find) are carried over as they are.

Run from the repository root. Needs git and the project's own environment (`uv run`).
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "ddflow" / "templates" / "upgrade" / "changes.toml"
sys.path.insert(0, str(ROOT))

from ddflow.core.events import version_key  # noqa: E402
from ddflow.services import upgrade_manifest as UM  # noqa: E402

#: Runs INSIDE a release's tree: those trees predate `services/upgrade_manifest`, so the
#: flattening is spelled out here (the same rule as `UM.knob_defaults`).
SNAPSHOT = r"""
import dataclasses, json
from ddflow import config as C
from ddflow.core.model import HANDLERS
cfg = C.Config()
skip = set(getattr(C, "_NOT_SECTIONS", ()))
knobs, docs = {}, getattr(C, "KNOB_DOCS", {})
for f in dataclasses.fields(cfg):
    sec = getattr(cfg, f.name)
    if f.name in skip or not dataclasses.is_dataclass(sec):
        continue
    for g in dataclasses.fields(sec):
        knobs[f"{f.name}.{g.name}"] = json.loads(json.dumps(getattr(sec, g.name), default=str))
print(json.dumps({"knobs": knobs, "event_kinds": sorted(HANDLERS),
                  "docs": {k: docs.get(k, "") for k in knobs}}))
"""

#: The default flips found by the backfill, said in words: what a project that never set
#: the knob sees after upgrading. Keyed (version, knob).
EFFECTS = {
    ("0.1.9", "dedupe.on_match"): (
        "an add that reads like an existing record is filed with a warning instead of being refused"
    ),
    ("0.1.10", "dedupe.on_match"): (
        "an add that reads like an existing record is refused (exit 3) until answered with "
        "--new, --extends, --duplicate-of or --related"
    ),
    ("0.1.13", "worktree.root"): (
        "new worktrees are made under .ddflow/worktrees inside the repository instead of "
        "../.ddflow-worktrees; existing ones stay where they are"
    ),
    ("0.1.17", "dedupe.kinds"): "rules are checked for near-duplicates too",
    ("0.1.17", "worktree.max_parallel"): (
        "the worktree cap follows the schedule's parallelism limit instead of a fixed 4"
    ),
    ("0.2.0", "review.delta_default"): (
        "a plain re-review sends the item's whole diff with the previous findings and their "
        "triage; --delta asks for only the commits since the last review"
    ),
    ("0.2.0", "schedule.signals"): (
        "adds the gate_failure_ratio signal and explicit thresholds for disk, memory and "
        "reviewer latency to adaptive flow control"
    ),
}

_VERSION = re.compile(r'^__version__ = "([^"]+)"', re.M)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True
    ).stdout


def releases() -> list[tuple[str, str, str]]:
    """(version, commit, date) of every release, oldest first: the first commit at which
    each new, higher `__version__` is declared."""
    log = git(
        "log",
        "--reverse",
        "--format=%H %ad",
        "--date=short",
        "-G__version__ = ",
        "--",
        "ddflow/__init__.py",
    )
    out: list[tuple[str, str, str]] = []
    for line in log.splitlines():
        sha, date = line.split()
        m = _VERSION.search(git("show", f"{sha}:ddflow/__init__.py"))
        if not m:
            continue
        v = m.group(1)
        if not out or version_key(v) > version_key(out[-1][0]):
            out.append((v, sha, date))
    return out


def snapshot(tree: Path) -> dict:
    """Knobs, their docs and the event kinds of the `ddflow` package under ``tree``."""
    r = subprocess.run(
        [sys.executable, "-c", SNAPSHOT],
        cwd=tree,
        env={"PYTHONPATH": str(tree), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise SystemExit(f"snapshot of {tree} failed:\n{r.stderr}")
    return json.loads(r.stdout)


def snapshot_commit(sha: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="ddflow-manifest-") as tmp:
        data = subprocess.run(
            ["git", "-C", str(ROOT), "archive", sha, "ddflow"], check=True, capture_output=True
        ).stdout
        with tarfile.open(fileobj=BytesIO(data)) as tar:
            tar.extractall(tmp, filter="data")
        return snapshot(Path(tmp))


#: The longest one-line why taken from a knob's doc.
WHY_CHARS = 240

#: The kinds no snapshot can find: written by hand, carried over by a backfill.
MANUAL = ("refresh", "repair", "feature")

HEADER = (
    " The ddflow upgrade manifest (B-upgrade.2-changes): what changed in each release.",
    " Read by ddflow.services.upgrade_manifest; rebuilt by `scripts/upgrade_manifest.py",
    " backfill`, which keeps every hand-written why/effect/impact/enable and every refresh,",
    " repair and feature entry. Defaults are JSON-encoded strings. Changes not yet released",
    " are one fragment file each under unreleased/.",
)


def _why(doc: str) -> str:
    """A knob doc's first sentence, as the one-line why."""
    first = re.split(r"(?<=[.!?])\s+(?=[A-Z`(])", doc.strip(), maxsplit=1)[0]
    if len(first) <= WHY_CHARS:
        return first
    return first[: WHY_CHARS - 3].rstrip(" .") + "..."


def _enc(v: object) -> str:
    return json.dumps(v, sort_keys=True)


def diff(version: str, a: dict, b: dict, kept: dict) -> list[dict]:
    """The changes from snapshot ``a`` to ``b``, as manifest entries; ``kept`` holds the
    hand-written fields of entries already in the manifest, keyed (kind, key)."""
    out: list[dict] = []
    ka, kb = a["knobs"], b["knobs"]
    for k in sorted(kb):
        prior = kept.get(("knob_added", k)) or kept.get(("knob_changed", k)) or {}
        why = prior.get("why") or _why(b["docs"].get(k, ""))
        if k not in ka:
            e = {"kind": "knob_added", "key": k, "new": _enc(kb[k]), "why": why}
            e["impact"] = prior.get("impact") or "additive"
            out.append(e)
        elif ka[k] != kb[k]:
            e = {"kind": "knob_changed", "key": k, "old": _enc(ka[k]), "new": _enc(kb[k])}
            e["why"] = why
            e["effect"] = prior.get("effect") or EFFECTS.get((version, k), "")
            e["impact"] = prior.get("impact", "")
            out.append(e)
    for k in sorted(set(ka) - set(kb)):
        prior = kept.get(("knob_removed", k)) or {}
        out.append({"kind": "knob_removed", "key": k, "old": _enc(ka[k]), **prior})
    for k in sorted(set(b["event_kinds"]) - set(a["event_kinds"])):
        prior = kept.get(("event_kind_added", k)) or {}
        out.append({"kind": "event_kind_added", "key": k, "impact": "additive", **prior})
    for k in sorted(set(a["event_kinds"]) - set(b["event_kinds"])):
        out.append(
            {"kind": "event_kind_removed", "key": k, **(kept.get(("event_kind_removed", k)) or {})}
        )
    return [{f: v for f, v in e.items() if v != ""} for e in out]


def _fields(c: UM.Change) -> dict:
    """A parsed change back as the manifest's fields (defaults re-encoded)."""
    e = {"kind": c.kind, "key": c.key}
    if c.has_old:
        e["old"] = _enc(c.old)
    if c.has_new:
        e["new"] = _enc(c.new)
    for f in ("why", "effect", "impact", "enable"):
        if getattr(c, f):
            e[f] = getattr(c, f)
    return e


def _kept() -> dict[str, dict]:
    """What the existing manifest and fragments say, per version: (kind, key) -> fields
    (their hand-written why/effect/impact/enable win), and the entries no snapshot finds."""
    if not MANIFEST.exists():
        return {}
    out: dict[str, dict] = {}
    for v, _d, cs in UM.load(MANIFEST).releases:
        bucket = out.setdefault(v, {"by_key": {}, "manual": []})
        for c in cs:
            if c.kind in MANUAL:
                bucket["manual"].append(_fields(c))
            else:
                bucket["by_key"][(c.kind, c.key)] = _fields(c)
    return out


def fragment_name(e: dict) -> str:
    """The fragment file of one unreleased change: `<kind>.<key>.toml`."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{e['kind']}.{e['key']}") + ".toml"


def _q(text: str) -> str:
    """A TOML basic string: JSON's escapes are TOML's."""
    return json.dumps(text)


def _entry(header: str, e: dict) -> list[str]:
    return ["", header, *(f"{f} = {_q(v)}" for f, v in e.items())]


def assign_unreleased(kept: dict[str, dict], versions: list[str]) -> dict[str, dict]:
    """When a version has been cut since the last backfill, the entries written for
    `unreleased` (fragments) belong to it: their hand-written why, effect, impact and
    enable, and their refresh, repair and feature entries, move to the newest release
    that the manifest does not list yet."""
    out = dict(kept)
    if UM.UNRELEASED in out and versions and versions[-1] not in out:
        out[versions[-1]] = out.pop(UM.UNRELEASED)
    return out


def render(base: tuple[str, dict], releases: list[tuple[str, str, list[dict]]]) -> str:
    """The manifest file: the header, the base snapshot and every release's entries."""
    version, snap = base
    lines = [f"#{line}" for line in HEADER]
    lines += ["schema_version = 1", "", "[base]", f"version = {_q(version)}", "event_kinds = ["]
    lines += [f"    {_q(k)}," for k in snap["event_kinds"]]
    lines += ["]", "", "[base.knobs]"]
    lines += [f"{_q(k)} = {_q(_enc(v))}" for k, v in sorted(snap["knobs"].items())]
    for v, d, entries in releases:
        lines += ["", "[[release]]", f"version = {_q(v)}", f"date = {_q(d)}"]
        for e in entries:
            lines += _entry("[[release.change]]", e)
    return "\n".join(lines) + "\n"


def backfill(base: str) -> tuple[str, dict[str, str]]:
    """The manifest text (the released history) and the unreleased fragments {name: text}."""
    rels = [r for r in releases() if version_key(r[0]) >= version_key(base)]
    if not rels:
        raise SystemExit(f"no release at or after {base}")
    snaps = [(v, d, snapshot_commit(sha)) for v, sha, d in rels]
    kept = assign_unreleased(_kept(), [v for v, _d, _s in snaps])
    empty: dict = {"by_key": {}, "manual": []}
    history = []
    for (_pv, _pd, prev), (v, d, cur) in itertools.pairwise(snaps):
        mine = kept.get(v, empty)
        history.append((v, d, diff(v, prev, cur, mine["by_key"]) + mine["manual"]))
    mine = kept.get(UM.UNRELEASED, empty)
    frags: dict[str, str] = {}
    for e in diff(UM.UNRELEASED, snaps[-1][2], snapshot(ROOT), mine["by_key"]) + mine["manual"]:
        body = ["# One unreleased change; cutting a version folds it into that release."]
        frags[fragment_name(e)] = "\n".join(body + _entry("[[change]]", e)) + "\n"
    return render((snaps[0][0], snaps[0][2]), history), frags


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="knobs and event kinds of a tree, as JSON")
    s.add_argument("--tree", default=str(ROOT))
    f = sub.add_parser("backfill", help="rebuild the manifest from the release commits")
    f.add_argument("--base", default="0.1.3")
    f.add_argument("--check", action="store_true", help="exit 1 if anything would change")
    a = ap.parse_args()
    if a.cmd == "snapshot":
        print(json.dumps(snapshot(Path(a.tree)), indent=2, sort_keys=True))
        return 0
    text, frags = backfill(a.base)
    UM.parse(text, list(frags.items()))  # never write a manifest the reader refuses
    fdir = MANIFEST.parent / UM.FRAGMENTS
    have = {p.name: p.read_text("utf-8") for p in fdir.glob("*.toml")} if fdir.is_dir() else {}
    current = MANIFEST.read_text("utf-8") if MANIFEST.exists() else ""
    if a.check:
        same = current == text and have == frags
        print("manifest up to date" if same else "manifest would change")
        return 0 if same else 1
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(text, "utf-8")
    fdir.mkdir(exist_ok=True)
    for name in set(have) - set(frags):
        (fdir / name).unlink()
    for name, body in frags.items():
        (fdir / name).write_text(body, "utf-8")
    print(f"wrote {MANIFEST.relative_to(ROOT)} and {len(frags)} unreleased fragment(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
