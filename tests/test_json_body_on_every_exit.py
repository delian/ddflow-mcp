"""Bug Bac9d773a44: `--json search` and `--json session list` printed no body when refused.

The class: a `--json` command whose refusal path prints only to stderr leaves a machine
reader with empty stdout. Every declared command, run at its refusal path, answers a JSON
body on stdout; `search` and `session list` carry the same envelope as their success."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.cli import build_parser

#: Commands that serve or wait forever, or run an external tool for minutes: not a refusal path.
LONG_RUNNING = {("mcp",), ("wait",), ("ci",), ("tests",), ("gate", "run")}
#: Commands whose --json still prints text (or two documents, `adopt`): a separate gap, listed
#: so the sweep stays green on it and fails on any NEW command that prints no JSON.
TEXT_ONLY = {
    ("replay",),
    ("reviewers", "detect"),
    ("reviewers", "list"),
    ("reviewers", "presets"),
    ("reviewers", "test"),
    ("adopt",),
    ("hooks", "session-start"),
}
BOGUS = "NOPE-bogus"
TIMEOUT = 60


def _leaves(p, path=()):
    subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        yield path, p
        return
    for sa in subs:
        seen = set()
        for name, sp in sa.choices.items():
            if id(sp) not in seen:
                seen.add(id(sp))
                yield from _leaves(sp, (*path, name))


def _argv(path, p):
    """The command's words, with a bogus value for every required argument."""
    out = list(path)
    for a in p._actions:
        if not a.required or isinstance(a, argparse._HelpAction):
            continue
        if a.option_strings:
            out.append(a.option_strings[0])
            if a.nargs != 0:
                out.append((a.choices and next(iter(a.choices))) or BOGUS)
        else:
            out.append((a.choices and next(iter(a.choices))) or BOGUS)
    return out


LEAVES = [(path, _argv(path, p)) for path, p in _leaves(build_parser()) if path not in LONG_RUNNING | TEXT_ONLY]


def _run(repo, argv):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    cmd = [sys.executable, "-m", "ddflow", "--repo", str(repo), "--json", *argv]
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=TIMEOUT, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired:
        return None
    return p


def test_every_command_prints_a_json_body_when_refused(repo):
    run_cli(repo, "init")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda la: _run(repo, la[1]), LEAVES))
    bad = []
    for (_path, argv), p in zip(LEAVES, results, strict=True):
        if p is None:
            bad.append(f"{' '.join(argv)}: timed out")
            continue
        if p.returncode in (0,) and not p.stdout.strip():
            continue  # a command with nothing to say on success
        if p.stderr.lstrip().startswith("usage:"):
            continue  # the parser refused the line, before any command ran
        try:
            json.loads(p.stdout)
        except ValueError:
            if p.returncode != 0 or p.stdout.strip():
                bad.append(f"{' '.join(argv)}: rc={p.returncode} stdout={p.stdout[:60]!r}")
    assert not bad, "no JSON body on:\n" + "\n".join(bad)


@pytest.mark.parametrize(
    ("argv", "kind"),
    [
        (("search", "x", "--source", "bogus"), "search"),
        (("session", "list", "--state", "bogus"), "session"),
    ],
)
def test_refused_search_and_session_list_keep_the_list_envelope(repo, argv, kind):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "--json", *argv)
    assert code == 3, err
    body = json.loads(out)
    assert body["record_kind"] == kind and body["rows"] == [] and body["total"] == 0
    assert body["reason"] and body["reason"] in err
    assert body["schema"].startswith("list@")
