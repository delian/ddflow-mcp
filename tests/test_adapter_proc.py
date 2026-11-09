"""The `Proc` port: the same cases against the shipped `FakeProc` (recorded + scripted) and
the real module functions (`ddflow.adapters.proc`), pinned to the port's contract: `run`
answers a `returncode`, `capture` answers text and never raises for the command's own
trouble, `spawn_shell` answers a live process.

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

from ddflow.adapters import proc as real_proc
from ddflow.adapters.fakes.proc import FakeProc


def proc_cases(io) -> None:
    # every method is a callable answering the port's shape
    ran = io.run(["printf", "x"], capture_output=True, text=True)
    assert isinstance(ran.returncode, int)
    done = io.capture(["true"], timeout=5.0)
    assert isinstance(done.stdout, str) and done.ran is True
    p = io.spawn_shell("true")
    assert isinstance(p.wait(), int)


CONFORMANCE = {
    "Proc": {
        "real": lambda monkeypatch: real_proc,
        "cases": proc_cases,
        "fake": lambda cls: cls(script=[{}]),
    }
}


def test_the_fake_records_and_scripts_runs():
    io = FakeProc(script=[{"returncode": 3, "stdout": "out"}])
    got = io.run("cmd", "arg")
    assert got.returncode == 3 and got.stdout == "out"
    assert io.calls == [("run", ("cmd", "arg"), {})]
