"""`ddflow docs check` help walk speed (DX-docs-check-speed): ddflow's own ``--help`` is
walked from the argparse registry in process, so the walk of the whole tree owes no import
per verb and answers in seconds, not minutes — and it must see exactly the flag set the
spawned ``ddflow <verb> --help`` prints, or the check's findings would drift.

The fixtures are real ``ddflow`` runs; nothing is mocked but the timing.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ddflow.infra import proc as P
from ddflow.services import docscheck_help as H

_ROOT = Path(__file__).resolve().parents[1]

#: The walk owes no spawn for ddflow itself, so answering the WHOLE tree — every verb chain
#: the registry declares — must be a sub-10 s job. The old walk spawned once per verb chain
#: at ~1.5 s per import, ~4 minutes in all; the budget demands the in-process walk, and any
#: accidental return to per-verb spawning breaks it by an order of magnitude.
WALK_BUDGET_S = 10.0
#: The registry declares ~199 parsers today; the eager walk must have walked the tree, not
#: sampled it (a root-only walk would also be fast, and would check nothing).
MIN_CHAINS = 150
#: Real chains the spawned walk is compared against: the root, top verbs, a nested chain and
#: one that is no verb at all. Each is ONE `python -m ddflow` run, so the sample stays small.
SAMPLE = [(), ("brief",), ("config", "set"), ("docs", "check"), ("gate", "skip")]


def _spawn(chain: tuple[str, ...]) -> P.Captured:
    return P.capture([sys.executable, "-m", "ddflow", *chain, "--help"], timeout=90, cwd=_ROOT)


def test_the_ddflow_help_walk_is_in_process_and_under_ten_seconds():
    idx = H.HelpIndex(H.command_runner(_ROOT, 60), ["ddflow"])
    start = time.monotonic()
    chains = idx.walk_all()
    elapsed = time.monotonic() - start

    assert chains >= MIN_CHAINS, f"walked {chains} chains: the tree was not walked whole"
    assert elapsed < WALK_BUDGET_S, f"the ddflow walk took {elapsed:.1f}s (budget 10s)"
    assert idx.unavailable == {}  # in-process: nothing could fail to run
    # every chain the walk prepared resolves to itself with the flags its help text defines
    for chain in (("ddflow",), ("ddflow", "config", "set"), ("ddflow", "docs", "check")):
        assert idx.resolve([*chain, "--never-a-real-flag-xyz"])[0] == list(chain)


def test_the_in_process_walk_agrees_with_the_spawning_walk():
    first = _spawn(SAMPLE[0])
    if not first.ran:  # the real paths move a test, but on this checkout ddflow runs
        pytest.skip(f"ddflow cannot run here: {first.error}")
    with ThreadPoolExecutor(max_workers=len(SAMPLE)) as pool:
        spawned = list(pool.map(_spawn, SAMPLE))
    run = H.command_runner(_ROOT, 120)
    for chain, by_spawn in zip(SAMPLE, spawned, strict=True):
        by_tree = run(["ddflow", *chain, "--help"])
        assert by_tree.ran and by_tree.returncode == 0
        assert by_spawn.ran and by_spawn.returncode == 0
        assert H.help_flags(by_spawn.stdout) == H.help_flags(by_tree.stdout), (
            f"ddflow {' '.join(chain)} --help flags drifted from the spawned walk"
        )


def test_a_word_that_is_no_verb_stops_the_chain_like_the_spawn_of_it():
    spawned = _spawn(("bogus-verb-chain",))  # exit 2: not a verb
    if not spawned.ran:
        pytest.skip(f"ddflow cannot run here: {spawned.error}")
    idx = H.HelpIndex(H.command_runner(_ROOT, 60), ["ddflow"])
    by_tree = idx.resolve(["ddflow", "bogus-verb-chain", "--nonesuch"])
    by_spawn = H.HelpIndex(lambda argv: _spawn(tuple(argv[1:-1])), ["ddflow"]).resolve(
        ["ddflow", "bogus-verb-chain", "--nonesuch"]
    )

    # the exit-2 word ends both chains AFTER `ddflow`: flags are judged by the root's help
    assert by_tree == by_spawn and by_tree[0] == ["ddflow"]


def test_a_spawned_tool_stays_bounded_by_the_timeout_knob():
    stall = [
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        "--help",
    ]
    done = H.command_runner(_ROOT, 1, in_process_commands=())(stall)
    assert done.timed_out and done.returncode is None
    # ... and the in-process walk has no I/O to time out: it answers at once, whatever the knob
    tree = H.command_runner(_ROOT, 1)(["ddflow", "--help"])
    assert tree.ran and tree.returncode == 0 and "--repo" in tree.stdout


def test_help_flags_of_the_root_text_still_guide_the_findings():
    text = H.command_runner(_ROOT, 60)(["ddflow", "--help"]).stdout
    flags = H.help_flags(text)
    assert {"--help", "--json", "--repo", "--agent"} <= flags
