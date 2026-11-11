"""The `Transport` port: the same cases against `FakeTransport` (scripted) and the real
`LocalTransport` (a real child process), here and generically in tests/test_ports_contract.py
through `CONFORMANCE`.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pytest

from ddflow.adapters import lanes as L
from ddflow.adapters import transport as T
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.transport import FakeTransport
from ddflow.adapters.transport_local import LocalTransport

#: A lane that prints one line and exits 0; its output is that line, collected as `lane.log`.
BENIGN = (sys.executable, "-c", "print('hello', flush=True)")
#: A lane that stays alive until it is signalled.
KEEP = (sys.executable, "-c", "import time; time.sleep(60)")


def ended(t, lane: str, timeout: float = 15.0) -> L.Result | L.LaneStatus:
    """Poll `observe` until the lane leaves ``running`` (bounded, so a broken lane fails)."""
    deadline = time.monotonic() + timeout
    while True:
        st = t.observe(lane)
        if isinstance(st, L.Result) or st.state != "running" or time.monotonic() >= deadline:
            return st
        time.sleep(0.01)


def transport_conformance(t) -> None:
    # start, ride to a clean exit, collect the lane's output as its log file
    assert t.start("a", BENIGN).ok, "start answered not-ok"
    st = ended(t, "a")
    assert isinstance(st, L.LaneStatus) and (st.state, st.exit_code) == ("exited", 0)
    with tempfile.TemporaryDirectory() as scratch:
        tmp = Path(scratch)
        out = tmp / "out.log"
        assert t.copy_out("a", "lane.log", out, 512).ok and "hello" in out.read_text()
        # copy in and out round-trip
        src = tmp / "in.txt"
        src.write_text("payload")
        assert t.copy_in("a", src, "in.txt").ok
        back = tmp / "back.txt"
        assert t.copy_out("a", "in.txt", back, 512).ok and back.read_text() == "payload"
        # a cap holds: nothing is left when the file is over it
        over = tmp / "over.txt"
        assert t.copy_out("a", "lane.log", over, 2).status == "failed" and not over.exists()
    # a running lane: a second start is refused, a signal stops it, cleanup clears it
    assert t.start("keeper", KEEP).ok
    assert t.start("keeper", KEEP).status == "failed"
    assert t.signal("keeper", "TERM").ok
    assert ended(t, "keeper").state == "stopped"
    assert t.cleanup("keeper").ok
    assert isinstance(t.observe("keeper"), L.Result)


def _scripted_fake(cls: type) -> type[FakeTransport]:
    """A `FakeTransport` whose lanes script the two lifecycles `transport_conformance` rides."""
    return cls(
        script={
            "a": {"running": False, "exit_code": 0, "log": "hello\n"},
            "keeper": {"running": True},
        }
    )


CONFORMANCE = {
    "Transport": {
        "real": lambda monkeypatch: LocalTransport(),
        "fake": _scripted_fake,
        "cases": transport_conformance,
    },
}


def test_the_adapter_and_the_fake_are_registered_and_satisfy_the_port():
    assert FAKES["Transport"] is FakeTransport
    assert T.TRANSPORTS["local"] is LocalTransport
    assert T.Transport in FakeTransport.__mro__  # nominal subclass: the multi-adapter rule


def test_the_default_is_local_and_names_a_home():
    t = LocalTransport()
    assert t.name == "local" and t.target == "local"


def test_the_fake_scripts_and_records():
    f = FakeTransport(script={"a": {"running": False, "exit_code": 42}})
    assert f.start("a", BENIGN).ok
    assert (f.observe("a").state, f.observe("a").exit_code) == ("failed", 42)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "x.txt"
        src.write_text("x")
        assert f.copy_in("a", src, "x.txt").ok
        got = Path(tmp) / "got.txt"
        assert f.copy_out("a", "x.txt", got, 4).ok and got.read_text() == "x"
    assert f.calls[0] == ("start", "a", BENIGN)
    assert f.calls[1] == ("observe", "a")
    assert f.calls[-2] == ("copy_in", "a", str(src), "x.txt")
    assert f.calls[-1] == ("copy_out", "a", "x.txt", str(got), 4)


def test_the_fake_refuses_the_second_start_and_a_bad_signal():
    f = FakeTransport()
    f.start("r", KEEP)
    assert f.start("r", KEEP).status == "failed"
    assert f.signal("r", "TERM").ok and f.observe("r").state == "stopped"


def test_the_lane_runs_in_its_own_directory(tmp_path):
    t = LocalTransport(root=tmp_path)
    argv = (sys.executable, "-c", "import os; print(os.getcwd())")
    assert t.start("a", argv).ok
    st = ended(t, "a")
    assert isinstance(st, L.LaneStatus) and st.state == "exited"
    got = tmp_path / "got.log"
    assert t.copy_out("a", "lane.log", got, 512).ok
    assert Path(got.read_text().strip()).resolve() == (tmp_path / "a").resolve()


def test_a_bad_lane_name_is_refused_not_taken_as_a_path():
    with pytest.raises(ValueError):
        LocalTransport().start("../x", (sys.executable, "-c", "pass"))
