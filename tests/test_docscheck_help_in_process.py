"""The docs check help walk of ddflow's own CLI, walked in process (Bc621160a82).

`ddflow docs check` used to spawn `ddflow <verb> --help` once per verb chain (~1.5 s per
import), ~4 minutes per check on this repo, while the process already carried the whole
argparse registry. ddflow's own help is now answered from that registry, and only a
non-ddflow tool keeps spawning. The equivalence is guarded here: the in-process flags
must be exactly the spawned walk's, chain for chain.
"""

import sys
from pathlib import Path

import pytest

from ddflow.infra import proc as P
from ddflow.services import docscheck_help as H

ROOT = Path(__file__).resolve().parents[1]

#: One chain per depth, plus an invalid verb at root and one inside: the spawn cost
#: (~1.5 s each) makes walking all ~200 chains here as slow as the bug it pins, and the
#: equivalence is this process's parse_args against its own spawn -- a sample of every
#: shape pins it.
CHAINS = [
    [],
    ["gate"],
    ["gate", "run"],
    ["config", "set"],
    ["docs", "check"],
    ["session"],
    ["decision", "add"],
    ["bogus"],
    ["gate", "bogus"],
]


def spawned(chain):
    return P.capture([sys.executable, "-m", "ddflow", *chain, "--help"], timeout=60, cwd=ROOT)


def test_the_in_process_help_flags_match_the_spawned_walk():
    for chain in CHAINS:
        inproc = H.in_process(chain)
        real = spawned(chain)
        assert inproc.ran == real.ran, chain
        assert (inproc.returncode == 0) == (real.returncode == 0), chain
        assert H.help_flags(inproc.stdout or inproc.stderr) == H.help_flags(
            real.stdout or real.stderr
        ), chain


def test_ddflows_help_walk_spawns_nothing(monkeypatch):
    spawned_calls = []

    def capture(argv, **kwargs):
        spawned_calls.append(list(argv))
        return P.Captured(error=TimeoutError("spawned on purpose"))

    monkeypatch.setattr(P, "capture", capture)
    idx = H.HelpIndex(H.command_runner(ROOT, 60), ["ddflow"])
    got = idx.resolve(["ddflow", "gate", "run", "--timeout"])
    assert spawned_calls == []  # ddflow's own help is replayed, never spawned
    assert got is not None
    chain, flags = got
    assert chain == ["ddflow", "gate", "run"]
    assert "--timeout" in flags
    assert not idx.unavailable
