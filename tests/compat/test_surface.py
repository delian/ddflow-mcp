"""Surface snapshots: what each release exposes, compared with the one before it
(B-uni-compat-tests; decision D-compat; docs/ddflow/compatibility.md).

tests/compat/surfaces/<version>.json is what a RELEASED ddflow exposed -- its CLI command tree
with every flag, its MCP tools with every argument, its config knobs with their defaults,
its event kinds -- captured by tests/compat/surface.py run under that release's own wheel
(`scripts/ci/compat-matrix surfaces`). The current code is captured live. Then

- knobs and event kinds: each snapshot must equal the upgrade manifest replayed up to that
  release, so every difference between two releases is an entry the manifest declares (and a
  manifest entry that a release did not make fails the same way);
- commands, flags, tools and arguments: an ADDITIVE difference is allowed in any release; one
  that is not (a command, flag, tool or argument gone, a positional changed, an argument that
  became required or changed type) breaks a caller, and D-compat keeps those working through
  an alias. Each one that is not yet fixed is listed in DECLARED with the bug that tracks it,
  and a declaration that no longer matches a difference fails too, so the list cannot outlive
  what it excuses;
- the committed snapshots are what the release's wheel really prints (slow: a wheel build per
  release the first time, cached under /tmp/ddflow-compat/venvs).
"""

from __future__ import annotations

import json
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

import pytest
from surface import surface

from ddflow.core.events import version_key
from ddflow.services import upgrade_manifest as UM

ROOT = Path(__file__).resolve().parents[2]
SURFACES = Path(__file__).resolve().parent / "surfaces"
VERSIONS = sorted((p.stem for p in SURFACES.glob("*.json")), key=version_key)

#: (from release, to release, difference) -> the bug that tracks it. Nothing else may differ
#: non-additively. Remove an entry when its bug is fixed.
DECLARED: dict[tuple[str, str, str], str] = {
    ("0.1.15", "0.1.18", "tool argument removed: ddflow_rule_list.json"): "Bf84a50bce3",
    ("0.1.15", "0.1.18", "tool argument removed: ddflow_rule_list.limit"): "Bf84a50bce3",
    ("0.1.15", "0.1.18", "tool argument removed: ddflow_rule_remove.reason"): "Bf84a50bce3",
}


def load(version: str) -> dict:
    return json.loads((SURFACES / f"{version}.json").read_text())


def breaking(a: dict, b: dict) -> list[str]:
    """What ``b`` took away from, or changed in, what ``a`` exposed: not what it added."""
    out: list[str] = []
    for path, cmd in a["cli"].items():
        new = b["cli"].get(path)
        if new is None:
            out.append(f"command removed: {path}")
            continue
        out += [f"flag removed: {path} {flag}" for flag in cmd["flags"] if flag not in new["flags"]]
        if cmd["positionals"] != new["positionals"]:  # a new positional is required of every caller
            out.append(f"positionals changed: {path}")
    for name, args in a["tools"].items():
        new = b["tools"].get(name)
        if new is None:
            out.append(f"tool removed: {name}")
            continue
        for arg, (kind, required) in args.items():
            if arg not in new:
                out.append(f"tool argument removed: {name}.{arg}")
            elif new[arg][0] != kind:
                out.append(f"tool argument retyped: {name}.{arg}")
            elif new[arg][1] and not required:
                out.append(f"tool argument became required: {name}.{arg}")
        out += [
            f"tool argument added as required: {name}.{arg}"
            for arg, (_k, required) in new.items()
            if required and arg not in args
        ]
    return out


def test_the_snapshots_cover_the_matrix() -> None:
    """A snapshot that silently went missing would make every comparison below vacuous."""
    assert {"0.1.3", "0.1.10", "0.1.15", "0.1.18"} <= set(VERSIONS)


@pytest.mark.parametrize("version", VERSIONS)
def test_a_release_has_exactly_the_knobs_and_event_kinds_the_manifest_gives_it(
    version: str,
) -> None:
    snap = load(version)
    assert snap["version"] == version
    knobs, kinds = UM.replay(upto=version)
    assert snap["knobs"] == knobs, (
        "a knob difference between releases the manifest does not declare"
    )
    assert snap["event_kinds"] == sorted(kinds)


def test_the_current_code_has_exactly_the_knobs_and_event_kinds_the_manifest_gives_it() -> None:
    live = surface()
    knobs, kinds = UM.replay()
    assert live["knobs"] == knobs
    assert live["event_kinds"] == sorted(kinds)


def _pairs() -> list[tuple[str, str]]:
    names = [*VERSIONS, "current"]
    return list(pairwise(names))


def test_no_release_breaks_what_the_one_before_it_exposed_unless_declared() -> None:
    live = surface()
    found: set[tuple[str, str, str]] = set()
    for old, new in _pairs():
        b = live if new == "current" else load(new)
        found |= {(old, new, d) for d in breaking(load(old), b)}
    undeclared = sorted(found - set(DECLARED))
    assert not undeclared, (
        "a non-additive interface change with no declaration (D-compat: keep the old name "
        "working through an alias, or file the bug and list it in DECLARED):\n"
        + "\n".join(map(str, undeclared))
    )
    stale = sorted(set(DECLARED) - found)
    assert not stale, f"declared differences that no longer appear (fixed? remove them): {stale}"


def test_the_breaking_detector_sees_what_it_is_for() -> None:
    a = {
        "cli": {"ddflow": {"flags": ["--x", "--y"], "positionals": ["a"]}, "ddflow gone": {}},
        "tools": {"t": {"k": ["string", False], "r": ["string", False]}, "gone": {}},
    }
    b = {
        "cli": {"ddflow": {"flags": ["--x"], "positionals": ["b"]}},
        "tools": {"t": {"k": ["integer", False], "r": ["string", True], "n": ["string", True]}},
    }
    assert breaking(a, b) == [
        "flag removed: ddflow --y",
        "positionals changed: ddflow",
        "command removed: ddflow gone",
        "tool argument retyped: t.k",
        "tool argument became required: t.r",
        "tool argument added as required: t.n",
        "tool removed: gone",
    ]
    assert breaking(b, b) == []  # nothing is a break against itself
    wider = {"cli": {"ddflow": {"flags": ["--x", "--z"], "positionals": ["b"]}}, "tools": {}}
    assert breaking({"cli": b["cli"], "tools": {}}, wider) == []  # a new flag is allowed
    longer = {"cli": {"ddflow": {"flags": ["--x"], "positionals": ["b", "c"]}}, "tools": {}}
    assert breaking({"cli": b["cli"], "tools": {}}, longer) == ["positionals changed: ddflow"]


@pytest.mark.slow
@pytest.mark.parametrize("version", VERSIONS)
def test_a_committed_snapshot_is_what_the_releases_wheel_prints(version: str) -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci" / "compat-matrix"), "venv", version],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    printed = subprocess.run(
        [f"{out}/bin/python", "-I", str(Path(__file__).with_name("surface.py"))],
        capture_output=True,
        text=True,
        check=True,
        cwd="/",
    ).stdout
    assert json.loads(printed) == load(version)
