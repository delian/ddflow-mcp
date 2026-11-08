"""The `Digest` port: one set of cases against `ddflow.core.digest` (until its home moves
under `ddflow.adapters`, ADP-migrate-homes) and the shipped `FakeDigest`.

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

from ddflow.adapters.fakes.digest import FakeDigest


def digest_cases(d) -> None:
    a = d.content_digest("hello")
    assert a == d.content_digest("hello") and a != d.content_digest("hellp")
    assert d.content_digest("hello", length=7) == a[:7]
    assert d.content_digest("hello", length=0) == ""
    assert d.content_digest("é") == d.content_digest("é".encode())
    assert d.canonical({"b": 1, "a": [1, 2]}) == d.canonical({"a": [1, 2], "b": 1})
    assert d.canonical({"a": 1}) != d.canonical({"a": 2})
    assert d.of_obj({"b": 1, "a": 2}) == d.of_obj({"a": 2, "b": 1})
    assert d.of_obj({"a": 1}) != d.of_obj({"a": 2})
    assert len(d.of_obj({"a": 1}, length=6)) == 6
    assert d.of_obj({"a": 1}, size=64, length=1) == d.of_obj({"a": 1}, size=64)[:1]
    assert len(d.of_obj({"a": 1}, size=48)) == 96  # `size` is bytes, however large


def _real(monkeypatch):
    from ddflow.adapters import digest

    return digest


CONFORMANCE = {"Digest": {"real": _real, "cases": digest_cases}}


def test_the_default_adapter_passes_the_digest_cases(monkeypatch):
    digest_cases(_real(monkeypatch))


def test_the_fake_passes_the_digest_cases():
    digest_cases(FakeDigest())


def test_the_fake_builds_no_more_than_it_keeps(monkeypatch):
    """`length` bounds the work: a huge `size` with `length=1` must not build a huge string,
    counted in hash rounds, not in output length."""
    import zlib

    from ddflow.adapters.fakes import digest as module

    rounds = []
    real = zlib.crc32

    def counted(*args):
        rounds.append(1)
        assert len(rounds) <= 4, "more hash rounds than one kept character needs"  # stop at once
        return real(*args)

    monkeypatch.setattr(module.zlib, "crc32", counted)
    assert len(FakeDigest().of_obj({"a": 1}, size=10**9, length=1)) == 1
