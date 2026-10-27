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
    #: top-level ddflow could not fail to run in-process (a broken CHAIN would note that
    #: chain, an environment fact, not a walk failure)
    assert "ddflow" not in idx.unavailable
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

    def spawn_only(argv: list[str]) -> P.Captured:
        return _spawn(tuple(argv[1:-1]))

    spawn_only.in_process_of = set()  # a SPAWNING walk: nothing answered in-process
    by_spawn = H.HelpIndex(spawn_only, ["ddflow"]).resolve(
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


def test_the_in_process_knob_accepts_a_single_string():
    #: a str is an `Iterable[str]` of its CHARS; named as itself it is one command, not six
    run = H.command_runner(_ROOT, 60, in_process_commands="ddflow")
    assert run.in_process_of == {"ddflow"}
    by_tree = run(["ddflow", "--help"])
    assert by_tree.ran and by_tree.returncode == 0 and "--repo" in by_tree.stdout


def test_the_first_chain_of_a_fresh_process_is_cached_too():
    was = H._HERE._built
    H._HERE._built = None  # a fresh process: the versioned memo does not exist yet
    try:
        first = H._HERE.help(["ddflow", "--help"])  # builds the memo; must land IN it
        memo = H._HERE._built[2]
        assert ("--help",) in memo and memo[("--help",)] is first
        assert H._HERE.help(["ddflow", "--help"]) is first  # replayed, not re-parsed
    finally:
        H._HERE._built = was


def test_one_in_process_command_cannot_blanket_the_others(monkeypatch):
    real = H._InProcessHelp.chains

    def chains(self, cmd):
        if cmd != "ddflow":
            raise RuntimeError("no registry here")
        yield from real(self, cmd)

    monkeypatch.setattr(H._InProcessHelp, "chains", chains)
    run = H.command_runner(_ROOT, 60, in_process_commands=["ddflow", "ddflowx"])
    idx = H.HelpIndex(run, ["ddflow", "ddflowx"])
    #: the broken command notes ITSELF (never some joined "ddflow ddflowx" key), and
    #: ddflow's own chains are still judged on their own -- however THEY ended
    assert idx.unavailable.get("ddflowx") == "could not walk: no registry here"
    assert "ddflow ddflowx" not in idx.unavailable
    assert idx._cache


def test_a_verb_chain_that_exits_nonzero_is_noted_chain_by_chain(monkeypatch):
    real = H._InProcessHelp.help

    def help_(self, argv):
        if argv == ["ddflow", "workflow", "state", "--help"]:
            return P.Captured(2, "", "usage: ddflow workflow state ...\n")
        return real(self, argv)

    monkeypatch.setattr(H._InProcessHelp, "help", help_)
    idx = H.HelpIndex(H.command_runner(_ROOT, 60), ["ddflow"])
    assert idx._cache[("ddflow", "workflow", "state")] is None
    assert "ddflow workflow state" in idx.unavailable  # the chain's own key, however deep


def test_a_registry_that_cannot_build_still_reports_could_not_run(monkeypatch):
    walker = H._InProcessHelp()  # fresh: _built is None, so help() builds the parser here

    def broken_parser(self):
        raise ImportError("no registry importable here")

    monkeypatch.setattr(type(walker), "parser", broken_parser)
    capped = walker.help(["ddflow", "--help"])
    assert not capped.ran and "no registry importable here" in f"{capped.error}"


def test_a_render_never_serves_a_mix_of_a_foreign_threads_print():
    import io
    import threading
    from contextlib import redirect_stdout

    letgo = threading.Event()

    def foreign_write():
        if letgo.wait(10):  # times out and ends ITSELF if the release below can never run
            print("--phantom-flag-never-declared")

    thread = threading.Thread(target=foreign_write)
    thread.start()
    real = io.StringIO()  # the stream the foreign print meant (a stand-in for the terminal)
    sink = io.StringIO()  # the render's own capture
    with redirect_stdout(H._RoutedTo(real, sink, threading.get_ident())):
        letgo.set()  # release the foreign print inside the window
        print("--renders-own-flag")
        thread.join()
    assert "--renders-own-flag" in sink.getvalue()  # the render captured only ITS thread
    assert "--phantom-flag-never-declared" not in sink.getvalue()  # never mixed INTO it
    assert "--phantom-flag-never-declared" in real.getvalue()  # forwarded, not swallowed


def test_the_memo_cannot_serve_an_other_versions_replay(monkeypatch):
    walker = H._InProcessHelp()
    #: a memo built when this process used another version answers for THAT version; the
    #: running version's reply must come from its own (re)build, not a stale key
    monkeypatch.setattr(walker, "_built", ("EVIL-VERSION", object(), {("--help",): P.Captured(0, "STALE HELP", "")}))
    capped = walker.help(["ddflow", "--help"])
    assert "STALE HELP" not in (capped.stdout or "")
    assert "--repo" in (capped.stdout or "")


def test_a_registry_that_exits_while_building_still_notes_could_not_run(monkeypatch):
    walker = H._InProcessHelp()

    def exiting_parser(self):
        raise SystemExit(2)

    monkeypatch.setattr(type(walker), "parser", exiting_parser)
    capped = walker.help(["ddflow", "--help"])
    assert not capped.ran and "registry build exited" in f"{capped.error}"
