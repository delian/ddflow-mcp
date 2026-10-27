"""The docs check help walk of ddflow's own CLI, walked in process (Bc621160a82).

`ddflow docs check` used to spawn `ddflow <verb> --help` once per verb chain (~1.5 s per
import), ~4 minutes per check on this repo, while the process already carried the whole
argparse registry. ddflow's own help is now answered from that registry, and only a
non-ddflow tool keeps spawning. The equivalence is guarded here: the in-process flags
must be exactly the spawned walk's, chain for chain, and the walk that does not spawn
must be the walk `check_docs` really uses.
"""

import subprocess
import sys
from pathlib import Path

from ddflow.infra import proc as P
from ddflow.services import docscheck as D
from ddflow.services import docscheck_help as H

ROOT = Path(__file__).resolve().parents[1]

#: One verbose chain per depth, known to answer its help TODAY: the spawn cost (~1.5 s
#: each) makes walking all ~200 chains here as slow as the bug it pins, and a chain that
#: stops being valid must fail the spawned walk FIRST (asserted below), so the sample can
#: never go stale silently.
REAL_CHAINS = [
    [],
    ["gate"],
    ["gate", "run"],
    ["config", "set"],
    ["docs", "check"],
    ["session"],
    ["decision", "add"],
]

#: An invalid verb at the root, and one inside a valid verb: replicated and spawned must
#: both refuse them the same way.
BOGUS_CHAINS = [["bogus"], ["gate", "bogus"]]


def spawned(chain):
    return P.capture([sys.executable, "-m", "ddflow", *chain, "--help"], timeout=60, cwd=ROOT)


def test_the_in_process_help_flags_match_the_spawned_walk():
    for chain in REAL_CHAINS:
        inproc = H.in_process(chain)
        real = spawned(chain)
        assert real.ran and inproc.ran, chain
        assert real.returncode == 0 and inproc.returncode == 0, chain  # it must STILL answer
        assert H.help_flags(inproc.stdout or inproc.stderr), chain
        assert H.help_flags(inproc.stdout or inproc.stderr) == H.help_flags(
            real.stdout or real.stderr
        ), chain
    for chain in BOGUS_CHAINS:
        inproc = H.in_process(chain)
        real = spawned(chain)  # invalid verbs: the spawned walk refuses them too
        assert inproc.ran == real.ran, chain
        assert inproc.returncode != 0 and real.returncode != 0, chain
        assert H.help_flags(inproc.stdout or inproc.stderr) == H.help_flags(
            real.stdout or real.stderr
        ), chain


def test_every_way_of_spelling_this_ddflow_is_replayed_not_spawned(monkeypatch):
    spawned_calls = []

    def capture(argv, **kwargs):
        spawned_calls.append(list(argv))
        return P.Captured(error=TimeoutError("spawned on purpose"))

    monkeypatch.setattr(P, "capture", capture)
    for spelling in (
        ["ddflow", "gate", "run", "--help"],
        [sys.executable, "-m", "ddflow", "gate", "--help"],
    ):
        out = H.command_runner(ROOT, 60)(list(spelling))
        assert out.ran and out.returncode == 0, spelling
    path_spelled = H.command_runner(ROOT, 60)(["/venv/bin/ddflow", "gate", "--help"])
    assert not path_spelled.ran  # a path names that binary; it is run, not replayed
    assert spawned_calls == [["/venv/bin/ddflow", "gate", "--help"]]


def test_the_docs_check_walk_replays_ddflow_and_still_spawns_other_tools(tmp_path, monkeypatch):
    """The walk `check_docs` runs: nothing spawns for a ddflow mention, a non-ddflow tool
    still spawns (it is the walk), and the flag the walk then judges is ddflow's own."""
    readme = "Run `ddflow gate skip --reason x`; another tool: `nix run --alpha`.\n"
    files = {"README.md": readme, "x.py": "'run' --alpha add_parser\n"}
    with open(tmp_path / "pyproject.toml", "w", encoding="utf-8") as f:
        f.write('[project]\nname = "x"\n[project.scripts]\nddflow = "x:main"\n')
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    spawned_calls = []

    def capture(argv, **kwargs):
        spawned_calls.append(list(argv))
        return P.Captured(error=TimeoutError("spawned on purpose"))

    monkeypatch.setattr(P, "capture", capture)
    rep = D.check_docs(
        tmp_path, runner=H.command_runner(tmp_path, 60), commands=["ddflow", "nix"]
    )
    assert not any("ddflow" in c for c in spawned_calls)  # ddflow's walk went in process
    assert [c for c in spawned_calls if c[0] == "nix"]  # the non-ddflow tool still spawns
    assert not rep.findings, rep.findings
    assert [n.kind for n in rep.notes] == ["help"]  # the spawn for `nix`, still taken

    def live(argv, **kwargs):
        return P.Captured(0, "usage: nix [--alpha A]\n", "")

    monkeypatch.setattr(P, "capture", live)
    rep = D.check_docs(
        tmp_path, runner=H.command_runner(tmp_path, 60), commands=["ddflow", "nix"]
    )
    assert rep.clean, rep.findings  # ddflow's --reason resolved by THIS ddflow's help
