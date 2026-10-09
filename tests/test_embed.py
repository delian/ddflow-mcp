"""The Embedder contract (B-uni-context-pack.3-embedder): a companion command that reads
{"texts": [...]} on stdin and prints {"model", "vectors"}, the model2vec path, the doctor
line, and the BM25 fallback that says so."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.services import embed as E


def _companion(tmp_path: Path, body: str) -> str:
    """A command (the interpreter running this test and a script) whose behaviour is `body`."""
    script = tmp_path / "embedder.py"
    script.write_text(
        "import json, sys\nreq = json.load(sys.stdin)\ntexts = req['texts']\n" + body,
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return f"{sys.executable} {script}"


def _cfg(command: str = "", model_dir: str = "", timeout_s: int = 20) -> Config:
    cfg = Config()
    cfg.rag.command, cfg.rag.model_dir, cfg.rag.timeout_s = command, model_dir, timeout_s
    return cfg


#: A tiny deterministic model: a text's vector counts its a's, b's and c's.
GOOD = (
    "vec = lambda t: [float(t.count(c)) for c in 'abc']\n"
    "print(json.dumps({'model': 'abc-1', 'vectors': [vec(t) for t in texts]}))\n"
)


def test_a_companion_that_keeps_the_contract_answers(tmp_path):
    got = E.embed(tmp_path, _cfg(_companion(tmp_path, GOOD)), ["aab", "c"])
    assert got.ok and got.backend == E.COMPANION and got.model == "abc-1"
    assert got.vectors == ((2.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def test_nothing_to_embed_answers_without_starting_the_companion(tmp_path):
    got = E.embed(tmp_path, _cfg("/nonexistent/embedder"), [])
    assert got.ok and got.vectors == ()


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("sys.exit(3)\n", "exited 3"),
        ("print('not json')\n", "did not print JSON"),
        ("print('[1]')\n", "not an object"),
        ("print(json.dumps({'vectors': [[1.0]] * len(texts)}))\n", "no model id"),
        ("print(json.dumps({'model': 'm', 'vectors': [[1.0]]}))\n", "1 vectors for 2 texts"),
        (
            "print(json.dumps({'model': 'm', 'vectors': [[1.0], [1.0, 2.0]]}))\n",
            "different lengths",
        ),
        ("print(json.dumps({'model': 'm', 'vectors': [[], []]}))\n", "empty"),
        ('print(\'{"model": "m", "vectors": [[NaN], [1.0]]}\')\n', "not finite"),
        ("print(json.dumps({'model': 'm', 'vectors': [['x'], [1.0]]}))\n", "not finite"),
        ("print(json.dumps({'model': 'm', 'vectors': [[True], [1.0]]}))\n", "not finite"),
    ],
)
def test_a_companion_that_breaks_the_contract_is_unavailable_with_the_reason(tmp_path, body, why):
    got = E.embed(tmp_path, _cfg(_companion(tmp_path, body)), ["a", "b"])
    assert got.unavailable and got.vectors == () and why in got.reason


def test_a_slow_companion_times_out_and_a_missing_one_does_not_start(tmp_path):
    slow = _companion(tmp_path, "import time\ntime.sleep(30)\n")
    got = E.embed(tmp_path, _cfg(slow, timeout_s=1), ["a"])
    assert got.unavailable and "timed out after 1s" in got.reason
    gone = E.embed(tmp_path, _cfg("/nonexistent/embedder"), ["a"])
    assert gone.unavailable and "unavailable" in gone.reason


def test_nothing_configured_is_unavailable_and_says_how_to_configure_it(tmp_path):
    got = E.embed(tmp_path, _cfg(), ["a"])
    assert got.unavailable and "[rag].command" in got.reason and "[rag].model_dir" in got.reason


def test_a_model_dir_that_is_not_a_directory_never_reaches_a_download(tmp_path):
    got = E.embed(tmp_path, _cfg(model_dir=str(tmp_path / "no-such-model")), ["a"])
    assert got.unavailable
    assert "not a directory" in got.reason or "not installed" in got.reason


def test_the_companion_wins_over_the_model_dir(tmp_path):
    cfg = _cfg(_companion(tmp_path, GOOD), model_dir=str(tmp_path))
    assert E.backend(cfg) == E.COMPANION and E.embed(tmp_path, cfg, ["a"]).model == "abc-1"


def test_cosine():
    assert E.cosine((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
    assert E.cosine((1.0, 0.0), (0.0, 1.0)) == 0.0
    assert E.cosine((0.0, 0.0), (1.0, 1.0)) == 0.0


def test_rerank_orders_by_closeness_and_keeps_bm25_order_on_ties(tmp_path):
    cfg = _cfg(_companion(tmp_path, GOOD))
    got = E.rerank(tmp_path, cfg, "aaa", ["ccc", "bbb", "aab", "aaa"])
    assert (got.mode, got.note, got.model) == (E.SEMANTIC, "", "abc-1")
    assert got.order == (3, 2, 0, 1)  # aaa, aab, then the two zero-closeness ones in BM25 order
    tie = E.rerank(tmp_path, cfg, "zzz", ["x", "y", "w"])  # all vectors zero: all tie
    assert tie.mode == E.SEMANTIC and tie.order == (0, 1, 2)


def test_rerank_without_an_embedder_keeps_the_bm25_order_and_says_so(tmp_path):
    got = E.rerank(tmp_path, _cfg(), "q", ["b", "a"])
    assert got.mode == E.BM25 and got.order == (0, 1)
    assert "semantic ranking unavailable" in got.note and "BM25 order kept" in got.note
    assert E.rerank(tmp_path, _cfg(), "q", []).order == ()
    broken = E.rerank(tmp_path, _cfg(_companion(tmp_path, "sys.exit(1)\n")), "q", ["a"])
    assert broken.mode == E.BM25 and "exited 1" in broken.note


def test_the_doctor_line_says_what_is_configured_and_whether_it_can_run(tmp_path):
    (none,) = E.doctor_notes(_cfg())
    assert none.startswith("embedder: none configured") and "BM25 only" in none
    (ok,) = E.doctor_notes(_cfg(f"{sys.executable} -V"))
    assert "companion command is configured" in ok and "does not run it" in ok
    (gone,) = E.doctor_notes(_cfg("no-such-embedder-xyz --x"))
    assert "'no-such-embedder-xyz'" in gone and "not installed" in gone and "BM25 only" in gone
    (dir_,) = E.doctor_notes(_cfg(model_dir=str(tmp_path / "missing")))
    assert "model_dir" in dir_ and ("not a directory" in dir_ or "not installed" in dir_)


def test_doctor_reports_the_embedder_as_a_note_not_a_problem(repo):
    from conftest import run_cli

    from ddflow.api import reporting

    assert run_cli(repo, "init")[0] == 0
    out = reporting.doctor(repo, agent="a1")
    assert any(n.startswith("embedder: none configured") for n in out.data["notes"])
    assert not any("embedder" in p for p in out.data["problems"])


def test_the_rag_knobs_are_checked():
    from ddflow.config_sections.rag import timeout_problem

    assert timeout_problem(60) == "" and timeout_problem(1) == ""
    for bad in (0, -1, 601, "ten", True, 1.5):
        assert timeout_problem(bad)
    assert json.dumps(Config().rag.__dict__) == '{"command": "", "model_dir": "", "timeout_s": 60}'


def test_the_shell_runner_feeds_stdin_only_when_asked():
    from ddflow.infra import proc as P

    assert P.run_shell("cat", timeout_s=10, stdin_text='{"a": 1}').out == '{"a": 1}'
    assert P.run_shell("cat", timeout_s=10).out == ""  # /dev/null, as every other caller
    slow = P.run_shell(
        "cat; sleep 30", timeout_s=1, stdin_text="x", on_tick=lambda: None, tick_s=0.2
    )
    assert slow.timed_out and slow.out == "x"  # the text went in once; a resumed wait takes none
