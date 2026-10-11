"""Bug B-proc-stdin-resume: `run_shell(stdin_text=...)` fed a request larger than the pipe
only as far as the first 64 KiB on Python 3.12.3 (the CI interpreter): the first
`communicate(input=..., timeout=tick)` wrote what the pipe took, and the RESUMED call
(`input=None`, as the stdlib documents) never re-registered stdin for writing -- CPython
tested the argument, not the saved input -- so a child that reads late waited for bytes
that never came and the line ran to its timeout. ddflow now feeds stdin itself."""

from __future__ import annotations

import time

import pytest

from ddflow.infra import proc as P


def test_a_late_reader_gets_a_request_larger_than_the_pipe_whole():
    big = "y" * (1 << 19)  # eight pipe buffers; the child reads only after the first tick
    ticks: list[int] = []
    start = time.monotonic()
    got = P.run_shell(
        "sleep 0.7; wc -c",
        timeout_s=20,
        stdin_text=big,
        on_tick=lambda: ticks.append(1),
        tick_s=0.2,
    )
    assert not got.timed_out, f"stdin stopped after the first pipe buffer ({got.elapsed_s}s)"
    assert got.code == 0 and got.out.strip() == str(len(big))
    assert ticks, "the child outlived a tick, so the resumed-wait path was exercised"
    assert time.monotonic() - start < 15


def test_a_child_that_never_reads_is_killed_with_its_writer_and_the_timeout_reported():
    got = P.run_shell(
        "sleep 30", timeout_s=1, stdin_text="z" * (1 << 20), on_tick=lambda: None, tick_s=0.2
    )
    assert got.timed_out and got.code is None
    assert got.elapsed_s < 10


def test_multibyte_text_arrives_encoded_whole():
    text = "héllo ✓\n" * 20000
    got = P.run_shell("wc -c", timeout_s=20, stdin_text=text, on_tick=lambda: None, tick_s=0.2)
    assert got.code == 0 and got.out.strip() == str(len(text.encode("utf-8")))


def _spawned(monkeypatch):
    made: list = []
    real = P.popen

    def spy(*a, **k):
        made.append(real(*a, **k))
        return made[-1]

    monkeypatch.setattr(P, "popen", spy)
    return made


def test_text_the_pipe_cannot_encode_raises_to_the_caller_and_leaves_no_child(monkeypatch):
    """Review finding: the writer thread must not swallow an encoding error -- the
    caller used to get UnicodeEncodeError from `communicate(input=)` -- and the child
    that was already started must not be left running."""
    made = _spawned(monkeypatch)
    with pytest.raises(UnicodeEncodeError):
        P._shell_group(
            "cat", timeout=5, input="x\ud800y", text=True, encoding="utf-8", errors="strict"
        )
    assert made and all(p.poll() is not None for p in made), "the started child was left alive"


def test_a_str_on_a_binary_pipe_is_refused_loudly_not_delivered_as_nothing(monkeypatch):
    made = _spawned(monkeypatch)
    with pytest.raises(TypeError):
        P._shell_group("cat", timeout=5, input="hello")
    assert made and all(p.poll() is not None for p in made)
