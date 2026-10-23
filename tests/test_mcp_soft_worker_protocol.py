"""SU-worker-proto: the versioned shim<->worker protocol and the worker entry point.

The worker is today's `Server` behind newline-delimited JSON frames over stdio pipes
(D-soft-upgrade-architecture). It is stateless per request: the connection state (protocol,
client info, agent, called_from) travels in each request frame. A worker that cannot speak the
shim's protocol major answers with a typed error so the shim classifies the upgrade HARD.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ddflow.surfaces import worker_protocol as WP

ROOT = Path(__file__).resolve().parents[1]
GOVERNS = ("ddflow/surfaces/worker.py", "ddflow/surfaces/worker_protocol.py")

CONN = {
    "protocol": "2025-06-18",
    "client_info": {"name": "t", "version": "1"},
    "agent": "a",
    "called_from": "/x",
}


# ---- the frames ------------------------------------------------------------------------


def test_a_frame_round_trips_as_one_line():
    f = WP.request(7, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, CONN)
    line = WP.encode(f)
    assert line.endswith("\n") and line.count("\n") == 1
    assert WP.decode(line) == f


def test_every_frame_kind_has_a_constructor_and_validates():
    frames = [
        WP.hello(shim_proto=1),
        WP.worker_hello(digest="d", version="1.2.3"),
        WP.request(1, {"method": "x"}, CONN),
        WP.response(1, {"jsonrpc": "2.0", "id": 1, "result": {}}),
        WP.note(1, {"jsonrpc": "2.0", "method": "notifications/progress"}),
        WP.drain(),
        WP.drained(),
        WP.bye("done"),
        WP.error("bad_frame", "nope", id=2),
    ]
    for f in frames:
        assert WP.decode(WP.encode(f)) == f


@pytest.mark.parametrize(
    "line,code",
    [
        ("not json\n", "bad_frame"),
        ("[1,2]\n", "bad_frame"),
        ('{"no_type": 1}\n', "bad_frame"),
        ('{"t": "mystery"}\n', "unknown_frame"),
        ('{"t": "req"}\n', "bad_frame"),
        ('{"t": "req", "id": "x", "msg": {}, "conn": {}}\n', "bad_frame"),
        ('{"t": "res", "id": 1}\n', "bad_frame"),
    ],
)
def test_a_malformed_frame_raises_a_typed_error(line, code):
    with pytest.raises(WP.ProtocolError) as e:
        WP.decode(line)
    assert e.value.code == code


def test_a_blank_line_is_not_a_frame():
    assert WP.decode("\n") is None


def test_a_frame_may_carry_unknown_extra_fields_from_a_newer_minor():
    f = {**WP.drain(), "future": 1}
    assert WP.decode(json.dumps(f) + "\n")["t"] == "drain"


# ---- negotiation ---------------------------------------------------------------------


def test_same_major_is_accepted():
    assert WP.negotiate(WP.hello(shim_proto=WP.PROTO_MAJOR)) is None


def test_a_shim_of_another_major_is_refused_with_a_typed_error():
    err = WP.negotiate(WP.hello(shim_proto=WP.PROTO_MAJOR + 1))
    assert err["t"] == "error" and err["code"] == "incompatible_shim_proto"
    assert err["worker_proto"] == WP.PROTO_MAJOR


def test_a_hello_without_a_protocol_is_refused():
    assert WP.negotiate({"t": "hello"})["code"] == "bad_frame"


# ---- the worker loop (in process, with a fake handler) --------------------------------


def run_worker(frames, handler, *, drain_after=True, timeout=10):
    """Run `worker.run` over in-memory streams; returns the frames it wrote."""
    from ddflow.surfaces import worker

    lines = [WP.encode(f) for f in frames]
    out = io.StringIO()
    t = threading.Thread(
        target=worker.run,
        args=(Path("."), iter(lines), out),
        kwargs={"handler": handler},
        daemon=True,
    )
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "the worker did not exit at EOF"
    return [json.loads(x) for x in out.getvalue().splitlines()]


def echo(conn, msg, notify):
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "result": {"agent": conn["agent"], "method": msg.get("method")},
    }


def test_the_worker_greets_first_then_answers_a_request():
    out = run_worker(
        [
            WP.hello(shim_proto=WP.PROTO_MAJOR),
            WP.request(1, {"jsonrpc": "2.0", "id": 9, "method": "ping"}, CONN),
        ],
        echo,
    )
    assert out[0]["t"] == "hello" and out[0]["worker_proto"] == WP.PROTO_MAJOR
    res = next(f for f in out if f["t"] == "res")
    assert res["id"] == 1 and res["msg"]["result"] == {"agent": "a", "method": "ping"}


def test_the_connection_state_of_each_request_reaches_the_handler():
    seen = []

    def handler(conn, msg, notify):
        seen.append(conn)

    other = {**CONN, "agent": "b", "protocol": "2024-11-05"}
    run_worker(
        [
            WP.hello(shim_proto=WP.PROTO_MAJOR),
            WP.request(1, {"method": "x"}, CONN),
            WP.request(2, {"method": "x"}, other),
        ],
        handler,
    )
    assert sorted(c["agent"] for c in seen) == ["a", "b"]
    assert {c["protocol"] for c in seen} == {"2025-06-18", "2024-11-05"}


def test_a_notification_gets_no_response_frame_but_is_acknowledged_by_id():
    out = run_worker(
        [
            WP.hello(shim_proto=WP.PROTO_MAJOR),
            WP.request(4, {"jsonrpc": "2.0", "method": "notifications/initialized"}, CONN),
        ],
        lambda c, m, n: None,
    )
    res = next(f for f in out if f["t"] == "res")
    assert res["id"] == 4 and res["msg"] is None


def test_progress_notes_from_the_handler_are_relayed_tagged_with_the_request():
    def handler(conn, msg, notify):
        notify({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"p": 1}})
        return {"jsonrpc": "2.0", "id": 1, "result": {}}

    out = run_worker([WP.hello(shim_proto=WP.PROTO_MAJOR), WP.request(5, {"id": 1}, CONN)], handler)
    notes = [f for f in out if f["t"] == "note"]
    assert notes and notes[0]["id"] == 5
    assert out.index(notes[0]) < out.index(next(f for f in out if f["t"] == "res"))


def test_a_crashing_handler_is_answered_with_an_internal_error_never_dropped():
    def boom(conn, msg, notify):
        raise RuntimeError("kaboom")

    out = run_worker(
        [
            WP.hello(shim_proto=WP.PROTO_MAJOR),
            WP.request(1, {"jsonrpc": "2.0", "id": 8, "method": "x"}, CONN),
            WP.request(2, {"jsonrpc": "2.0", "id": 9, "method": "y"}, CONN),
        ],
        boom,
    )
    res = {f["id"]: f["msg"] for f in out if f["t"] == "res"}
    assert res[1]["error"]["code"] == -32603 and "kaboom" in res[1]["error"]["message"]
    assert res[1]["id"] == 8 and 2 in res, "the worker must survive a failing request"


def test_a_malformed_frame_gets_an_error_frame_and_the_worker_carries_on():
    from ddflow.surfaces import worker

    lines = [
        WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR)),
        "garbage\n",
        WP.encode(WP.request(1, {"method": "x"}, CONN)),
    ]
    out = io.StringIO()
    worker.run(Path("."), iter(lines), out, handler=echo)
    frames = [json.loads(x) for x in out.getvalue().splitlines()]
    assert any(f["t"] == "error" and f["code"] == "bad_frame" for f in frames)
    assert any(f["t"] == "res" and f["id"] == 1 for f in frames)


def test_an_incompatible_shim_gets_a_typed_error_and_the_worker_exits():
    out = run_worker(
        [WP.hello(shim_proto=WP.PROTO_MAJOR + 1), WP.request(1, {"method": "x"}, CONN)], echo
    )
    assert out[0]["t"] == "error" and out[0]["code"] == "incompatible_shim_proto"
    assert not any(f["t"] == "res" for f in out), "no request may be served after a refused hello"


def test_a_request_before_hello_is_refused():
    out = run_worker([WP.request(1, {"method": "x"}, CONN)], echo)
    assert any(f["t"] == "error" and f["code"] == "no_hello" for f in out)
    assert not any(f["t"] == "res" for f in out)


def test_drain_finishes_the_inflight_request_then_says_drained_and_bye():
    started, release = threading.Event(), threading.Event()

    def slow(conn, msg, notify):
        started.set()
        threading.Timer(0.5, release.set).start()  # independent of how the worker reads input
        assert release.wait(10)
        return {"jsonrpc": "2.0", "id": 1, "result": {"done": True}}

    from ddflow.surfaces import worker

    def feed():
        yield WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR))
        yield WP.encode(WP.request(1, {"id": 1}, CONN))
        assert started.wait(10)
        yield WP.encode(WP.drain())
        # A drain that did not wait for the held call would say `drained` before its answer.
        release.wait(10)

    out = io.StringIO()
    worker.run(Path("."), feed(), out, handler=slow)
    kinds = [json.loads(x)["t"] for x in out.getvalue().splitlines()]
    assert kinds.index("res") < kinds.index("drained") < kinds.index("bye")


def test_requests_after_drain_are_refused_not_served():
    from ddflow.surfaces import worker

    release = threading.Event()
    served = []

    def slow(conn, msg, notify):
        served.append(msg["id"])
        assert release.wait(10)
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {}}

    def feed():
        yield WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR))
        yield WP.encode(WP.request(1, {"id": 1}, CONN))
        while not served:
            time.sleep(0.01)
        yield WP.encode(WP.drain())  # request 1 is still running: the drain is open
        yield WP.encode(WP.request(2, {"id": 2}, CONN))
        threading.Timer(0.3, release.set).start()

    out = io.StringIO()
    worker.run(Path("."), feed(), out, handler=slow)
    frames = [json.loads(x) for x in out.getvalue().splitlines()]
    assert any(f["t"] == "error" and f["code"] == "draining" and f["id"] == 2 for f in frames)
    assert served == [1] and not any(f["t"] == "res" and f["id"] == 2 for f in frames)


def test_concurrent_requests_are_answered_independently():
    from ddflow.surfaces import worker

    out = io.StringIO()

    def handler(conn, msg, notify):
        if msg["id"] == 1:  # the slow call finishes only once the fast answer is on the wire
            deadline = time.time() + 10
            while '"t":"res","id":2' not in out.getvalue() and time.time() < deadline:
                time.sleep(0.01)
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {}}

    lines = [
        WP.encode(f)
        for f in (
            WP.hello(shim_proto=WP.PROTO_MAJOR),
            WP.request(1, {"id": 1}, CONN),
            WP.request(2, {"id": 2}, CONN),
        )
    ]
    worker.run(Path("."), iter(lines), out, handler=handler)
    order = [json.loads(x)["id"] for x in out.getvalue().splitlines() if '"t":"res"' in x]
    assert order == [2, 1], "a slow call must not block a fast one"


def test_another_major_with_an_odd_state_key_is_still_refused_as_incompatible():
    err = WP.negotiate({"t": "hello", "shim_proto": WP.PROTO_MAJOR + 1, "state_key": "not-hex"})
    assert err["code"] == "incompatible_shim_proto"


def test_a_bare_hello_and_a_note_without_an_id_are_not_frames():
    for line in ('{"t":"hello"}\n', '{"t":"note","msg":{}}\n', '{"t":"hello","shim_proto":"1"}\n'):
        with pytest.raises(WP.ProtocolError):
            WP.decode(line)
    assert WP.decode('{"t":"note","id":null,"msg":{}}\n')["id"] is None


@pytest.mark.parametrize("bad", ["xyz", 123, ["a"], "abc", " ", "ab cd", "12 34"])
def test_a_malformed_state_key_is_refused_not_a_crash(bad):
    err = WP.negotiate({"t": "hello", "shim_proto": WP.PROTO_MAJOR, "state_key": bad})
    assert err["t"] == "error" and err["code"] == "bad_frame"


def test_a_valid_state_key_is_accepted():
    assert WP.negotiate({"t": "hello", "shim_proto": WP.PROTO_MAJOR, "state_key": "ab12"}) is None


def test_a_peer_bye_is_a_clean_goodbye_not_a_refusal():
    from ddflow.surfaces import worker

    out = io.StringIO()
    lines = [WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR)), WP.encode(WP.bye("done"))]
    code = worker.run(Path("."), iter(lines), out, handler=echo)
    last = [json.loads(x) for x in out.getvalue().splitlines()][-1]
    assert code == 0 and last["t"] == "bye" and last.get("reason") != "refused"


def test_eof_right_after_drain_still_puts_drained_before_bye():
    from ddflow.surfaces import worker

    for _ in range(20):
        out = io.StringIO()
        lines = [WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR)), WP.encode(WP.drain())]
        worker.run(Path("."), iter(lines), out, handler=echo)
        kinds = [json.loads(x)["t"] for x in out.getvalue().splitlines()]
        assert kinds.index("drained") < kinds.index("bye"), kinds


def test_an_internal_failure_while_dispatching_is_an_error_frame_not_a_dead_worker(monkeypatch):
    from ddflow.surfaces import worker

    real = threading.Thread

    class Failing(real):
        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(worker.threading, "Thread", Failing)
    out = io.StringIO()
    lines = [WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR)), WP.encode(WP.request(1, {}, CONN))]
    code = worker.run(Path("."), iter(lines), out, handler=echo)
    frames = [json.loads(x) for x in out.getvalue().splitlines()]
    assert any(f["t"] == "res" and f["id"] == 1 and "error" in f["msg"] for f in frames)
    assert code == 0


# ---- the real worker over real pipes -----------------------------------------------------


def _spawn(repo):
    return subprocess.Popen(
        [sys.executable, "-m", "ddflow.surfaces.worker", "--repo", str(repo)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=ROOT,
    )


def _git(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _read(p, timeout=30):
    box = []
    t = threading.Thread(target=lambda: box.append(p.stdout.readline()), daemon=True)
    t.start()
    t.join(timeout)
    assert box and box[0], "the worker did not answer"
    return WP.decode(box[0])


def test_the_real_worker_serves_tools_list_over_pipes(tmp_path):
    p = _spawn(_git(tmp_path / "r"))
    try:
        p.stdin.write(WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR)))
        p.stdin.flush()
        hello = _read(p)
        assert hello["t"] == "hello" and hello["worker_proto"] == WP.PROTO_MAJOR
        assert len(hello["digest"]) == 64 and hello["version"]
        p.stdin.write(
            WP.encode(WP.request(1, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, CONN))
        )
        p.stdin.flush()
        res = _read(p)
        assert res["t"] == "res" and res["msg"]["result"]["tools"]
    finally:
        p.stdin.close()
        p.wait(timeout=30)


def test_the_real_worker_exits_at_eof_with_a_bye(tmp_path):
    p = _spawn(_git(tmp_path / "r"))
    p.stdin.write(WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR)))
    p.stdin.flush()
    _read(p)
    p.stdin.close()
    last = _read(p)
    assert last["t"] == "bye"
    assert p.wait(timeout=30) == 0


def test_the_real_worker_keeps_stdout_for_frames_only(tmp_path):
    """A stray print or a child's output must not corrupt the frame stream."""
    p = _spawn(_git(tmp_path / "r"))
    try:
        p.stdin.write(WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR)))
        p.stdin.write(WP.encode(WP.request(1, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, CONN)))
        p.stdin.flush()
        assert _read(p)["t"] == "hello" and _read(p)["t"] == "res"
        p.stdin.close()
        assert _read(p)["t"] == "bye"  # and then nothing but EOF
        assert p.wait(timeout=30) == 0  # it exits instead of hanging after its bye
        rest = []
        reader = threading.Thread(target=lambda: rest.append(p.stdout.read()), daemon=True)
        reader.start()
        reader.join(10)
        assert rest == [""], "nothing may follow the bye (or the pipe never closed)"
    finally:
        p.stdin.close()
        p.wait(timeout=30)


def test_a_clean_drain_exits_zero_even_while_stdin_stays_open(tmp_path):
    p = _spawn(_git(tmp_path / "r"))
    try:
        p.stdin.write(WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR)))
        p.stdin.flush()
        _read(p)
        p.stdin.write(WP.encode(WP.drain()))
        p.stdin.flush()
        assert _read(p)["t"] == "drained" and _read(p)["t"] == "bye"
        assert p.wait(timeout=30) == 0  # stdin is still open: the exit is the drain's
    finally:
        p.kill()


def test_a_worker_hello_without_digest_and_version_is_not_a_frame():
    with pytest.raises(WP.ProtocolError):
        WP.decode('{"t":"hello","worker_proto":1}\n')
    assert WP.decode(WP.encode(WP.worker_hello(digest="d", version="1")))["digest"] == "d"


def test_a_drain_that_cannot_start_its_thread_is_an_error_and_the_worker_still_says_bye(
    monkeypatch,
):
    from ddflow.surfaces import worker

    real = threading.Thread

    class Failing(real):
        def start(self):
            if self.name == "ddflow-worker-drain":
                raise RuntimeError("can't start new thread")
            super().start()

    monkeypatch.setattr(worker.threading, "Thread", Failing)
    out = io.StringIO()
    lines = [WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR)), WP.encode(WP.drain())]
    code = worker.run(Path("."), iter(lines), out, handler=echo)
    frames = [json.loads(x) for x in out.getvalue().splitlines()]
    assert any(f["t"] == "error" and f["code"] == "internal" for f in frames)
    assert frames[-1]["t"] == "bye" and code == 0


def test_a_reply_that_is_not_json_is_an_error_frame_not_silence():
    def odd(conn, msg, notify):
        return {"jsonrpc": "2.0", "id": 1, "result": {"x": object()}}

    out = run_worker([WP.hello(shim_proto=WP.PROTO_MAJOR), WP.request(1, {"id": 1}, CONN)], odd)
    assert any(f["t"] == "error" and f["code"] == "unencodable" and f["id"] == 1 for f in out)


def test_main_reports_a_crashed_run_as_exit_1_even_after_a_drain_flag(monkeypatch):
    from ddflow.surfaces import worker

    def dying(repo, lines, out, **kw):
        kw["done"].set()
        raise RuntimeError("stdin broke")

    monkeypatch.setattr(worker, "run", dying)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert worker.main(["--repo", "."]) == 1


def test_nothing_is_written_after_bye():
    from ddflow.surfaces import worker

    class Feed:
        def __iter__(self):
            yield WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR))
            yield WP.encode(WP.drain())
            time.sleep(0.5)  # drained + bye are out by now
            yield WP.encode(WP.request(1, {"method": "x"}, CONN))

    out = io.StringIO()
    worker.run(Path("."), iter(Feed()), out, handler=echo)
    kinds = [json.loads(x)["t"] for x in out.getvalue().splitlines()]
    assert kinds[-1] == "bye" and kinds.count("bye") == 1


def test_a_hello_of_another_major_with_both_fields_is_refused_as_incompatible():
    frame = WP.decode('{"t":"hello","shim_proto":2,"worker_proto":2}\n')
    assert WP.negotiate(frame)["code"] == "incompatible_shim_proto"


def test_a_refused_hello_exits_3_the_ddflow_code_for_refused():
    from ddflow.surfaces import worker

    out = io.StringIO()
    code = worker.run(
        Path("."), iter([WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR + 1))]), out, handler=echo
    )
    assert code == 3


def test_a_reply_nested_beyond_the_recursion_limit_is_an_error_frame_too():
    def deep(conn, msg, notify):
        v: object = 1
        for _ in range(200_000):
            v = [v]
        return {"jsonrpc": "2.0", "id": 1, "result": {"x": v}}

    out = run_worker([WP.hello(shim_proto=WP.PROTO_MAJOR), WP.request(1, {"id": 1}, CONN)], deep)
    assert any(f["t"] == "error" and f["code"] == "unencodable" for f in out)


def test_a_deeply_nested_line_is_a_bad_frame_not_a_crash():
    with pytest.raises(WP.ProtocolError) as e:
        WP.decode("[" * 200_000 + "1" + "]" * 200_000 + "\n")
    assert e.value.code == "bad_frame"


def test_drained_and_bye_are_adjacent_even_when_a_bad_frame_arrives_meanwhile():
    from ddflow.surfaces import worker

    for _ in range(30):
        out = io.StringIO()
        lines = [
            WP.encode(WP.hello(shim_proto=WP.PROTO_MAJOR)),
            WP.encode(WP.drain()),
            "not-json\n",
        ]
        worker.run(Path("."), iter(lines), out, handler=echo)
        kinds = [json.loads(x)["t"] for x in out.getvalue().splitlines()]
        assert kinds[-2:] == ["drained", "bye"], kinds
