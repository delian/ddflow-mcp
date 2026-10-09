"""The shared budget vocabulary (core/budget.py) and, later, the context pack."""

from __future__ import annotations

import inspect

from ddflow.core import budget as B


def test_approx_tokens_is_the_estimate_the_brief_always_used():
    for text in ("", "a", "abcd", "x" * 399, "x" * 400, "é" * 41):
        assert B.approx_tokens(text) == max(1, len(text) // 4)
    assert B.chars_for(0) == 0 and B.chars_for(250) == 1000


def test_budget_states_its_unit():
    t = B.Budget(100, "tokens")
    assert (t.chars, t.tokens) == (400, 100)
    assert t.cost("x" * 40) == 10 and t.fits("x" * 400) and not t.fits("x" * 404)
    c = B.Budget(4000)
    assert (c.chars, c.tokens) == (4000, 1000)
    assert c.cost("x" * 10) == 10 and c.fits("x" * 4000) and not c.fits("x" * 4001)
    assert B.Budget(2, "chars").tokens == 1  # never zero: a budget of nothing is not a budget


def test_the_recall_default_is_4000_everywhere(monkeypatch):
    """It was 4000 in the API, the CLI parser, the MCP tool and the MCP bound; one constant
    now serves all four (the flag and the argument are one declaration), and the value is
    pinned here so a change of it is deliberate."""
    from ddflow.api.knowledge import retrieval
    from ddflow.surfaces.declared import knowledge as declared
    from ddflow.surfaces.tools import TOOLS

    assert B.RECALL_MAX_CHARS == 4000
    assert inspect.signature(retrieval.recall).parameters["max_chars"].default == 4000
    assert "default=RECALL_MAX_CHARS" in inspect.getsource(declared)

    seen = {}

    class _Api:
        def recall(self, repo, query, **kw):
            seen.update(kw)

    monkeypatch.setattr(declared, "_api", _Api)
    TOOLS["ddflow_recall"]["api"](None, {"query": "q"}, "")
    assert seen["max_chars"] == 4000
    TOOLS["ddflow_recall"]["api"](None, {"query": "q", "max_chars": 900}, "")
    assert seen["max_chars"] == 900


def test_the_brief_of_an_empty_project_reports_at_least_the_clamp(repo):
    """`approx_tokens` clamps at 1 where the old `len(text) // 4` could say 0; a brief always
    opens with its heading, so the two agree on the emptiest brief there is."""
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from conftest import run_cli

    run_cli(repo, "init")
    code, out, _err = run_cli(repo, "--json", "brief")
    body = json.loads(out)
    assert code == 0 and len(body["brief"]) >= 4  # the clamp and the plain // 4 coincide
    assert body["approx_tokens"] == len(body["brief"]) // 4 >= 1


# -- the pack (services/contextpack.py) -----------------------------------------------------


def _cand(source, ident, text, body=""):
    from ddflow.services import contextpack as CP

    row = {"id": ident, "title": text}
    hit = {"id": ident, "kind": source.upper(), "headline": text, "body": body, "raw": row}
    return CP.Candidate(source, ident, row, hit, text, body)


def test_the_pack_takes_one_hit_per_source_in_turn_and_keeps_the_first_whole():
    from ddflow.services import contextpack as CP

    cands = {
        "decisions": [_cand("decisions", f"D{i}", f"decision {i}", "d" * 50) for i in range(3)],
        "lessons": [_cand("lessons", f"L{i}", f"lesson {i}", "l" * 50) for i in range(3)],
    }
    p = CP.pack(cands, B.Budget(1))  # smaller than any hit: the first still answers
    assert p.shown == 1 and p.kept["decisions"][0].cite == "decisions:D0" and p.truncated
    p = CP.pack(cands, B.Budget(150))
    assert p.cited == ("decisions:D0", "lessons:L0") and p.truncated  # 61 characters each
    full = CP.pack(cands, B.Budget(100_000))
    assert full.shown == 6 and not full.truncated and full.note() == ""


def test_the_pack_folds_a_repeated_text_and_says_so():
    from ddflow.services import contextpack as CP

    cands = {
        "lessons": [_cand("lessons", "L1", "Run tests  in parallel", "use -n 16")],
        "memories": [_cand("memories", "M1", "run tests in PARALLEL", "use -n 16")],
    }
    p = CP.pack(cands, B.Budget(4000))
    assert p.shown == 1 and p.duplicates == 1 and not p.truncated
    assert "folded" in p.note() and "lessons" in p.kept
    assert CP.pack(cands, B.Budget(4000), dedupe=False).shown == 2


def test_a_hit_that_does_not_fit_is_skipped_but_a_smaller_one_behind_it_may(tmp_path):
    from ddflow.services import contextpack as CP

    cands = {
        "a": [_cand("a", "A1", "first", "x" * 10), _cand("a", "A2", "big", "y" * 500)],
        "b": [_cand("b", "B1", "tiny", "z")],
    }
    p = CP.pack(cands, B.Budget(100))
    assert [c.id for v in p.kept.values() for c in v] == ["A1", "B1"] and p.truncated


def test_recall_enforces_its_budget_once_for_the_cli_and_the_mcp_tool(repo):
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from conftest import run_cli

    run_cli(repo, "init")
    for i in range(6):
        run_cli(repo, "lesson", "add", "--id", f"L-p{i}", "--title", f"pack budget {i}",
                "--rule", f"rule {i} " + "q" * 300)  # fmt: skip
    wide = json.loads(run_cli(repo, "--json", "recall", "pack budget", "--limit", "6")[1])
    narrow = json.loads(
        run_cli(repo, "--json", "recall", "pack budget", "--limit", "6", "--max-chars", "900")[1]
    )

    def n(body):
        return sum(len(v) for v in body.values() if isinstance(v, list))

    assert n(wide) == 6 and 1 <= n(narrow) < 6, "the JSON surface obeys max_chars too"
    assert "truncated" not in wide and narrow["truncated"].startswith("truncated: showing")
    code, out, _ = run_cli(repo, "recall", "pack budget", "--limit", "6", "--max-chars", "900")
    assert code == 0 and "truncated at 900 chars" in out
    assert out.count("[L-p") == n(narrow), "the CLI prints exactly what the API kept"


def test_a_folded_repeat_is_said_on_the_cli_too(monkeypatch, capsys):
    from types import SimpleNamespace

    from ddflow.core import outcome as O
    from ddflow.surfaces.commands import knowledge as K

    row = {"id": "L1", "title": "same words", "rule": "alike"}
    data = {
        "results": {"lessons": []},
        "max_chars": 4000,
        "pack": {"truncated": False, "duplicates": 1, "cut_note": "", "fold_note": "1 repeated hit(s) folded into the first"},
        "_render": {"results": {"lessons": [row]}, "sources": (("lessons", "LESSON", "why"),)},
    }  # fmt: skip
    monkeypatch.setattr(K.A, "recall", lambda *a, **k: O.Outcome(kind="recall", data=data))
    args = SimpleNamespace(query="q", sources="", limit=3, max_chars=4000)
    ctx = SimpleNamespace(repo=None, json=False, requested_agent="")
    assert K.cmd_recall(args, ctx) == 0
    out = capsys.readouterr().out
    assert "… 1 repeated hit(s) folded into the first" in out and "Recall is a prompt" in out


def test_a_cut_and_a_fold_are_both_said_and_the_json_names_each(monkeypatch, capsys):
    from types import SimpleNamespace

    from ddflow.core import outcome as O
    from ddflow.surfaces.commands import knowledge as K

    row = {"id": "L1", "title": "t", "rule": "r"}
    pack = {
        "truncated": True, "duplicates": 2, "cut_note": "truncated: showing 1 of 5 hits within max_chars=9",
        "fold_note": "2 repeated hit(s) folded into the first",
    }  # fmt: skip
    data = {
        "results": {"lessons": [{"id": "L1", "kind": "LESSON"}]}, "max_chars": 9, "pack": pack,
        "_render": {"results": {"lessons": [row]}, "sources": (("lessons", "LESSON", "why"),)},
    }  # fmt: skip
    monkeypatch.setattr(K.A, "recall", lambda *a, **k: O.Outcome(kind="recall", data=data))
    args = SimpleNamespace(query="q", sources="", limit=3, max_chars=9)
    assert K.cmd_recall(args, SimpleNamespace(repo=None, json=False, requested_agent="")) == 0
    out = capsys.readouterr().out
    assert "… 2 repeated hit(s) folded" in out and "truncated at 9 chars" in out
    assert K.cmd_recall(args, SimpleNamespace(repo=None, json=True, requested_agent="")) == 0
    body = __import__("json").loads(capsys.readouterr().out)
    assert body["truncated"] == pack["cut_note"] and body["folded"] == pack["fold_note"]
