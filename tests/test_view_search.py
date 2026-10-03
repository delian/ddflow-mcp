"""`ddflow search`: one search across every source, with filters and three modes.

Each source is findable; filters narrow; exact and regex work; a pathological regex is
refused rather than run; a secret recorded in a session prompt never reaches the output;
an empty result says so (exit 2).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

SECRET = "sk-abcdefghijklmnop1234567890"


def _fixture(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Quarry phase")
    run_cli(repo, "phase", "add", "P2", "--title", "Other phase")
    run_cli(
        repo, "task", "add", "T1", "--phase", "P1", "--title", "Polish the zirconium widget",
        "--body", "grind the edges", "--globs", "a.py",
    )  # fmt: skip
    run_cli(repo, "task", "add", "T2", "--phase", "P2", "--title", "Zirconium in phase two")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "xylophone crashes on empty input")
    run_cli(
        repo, "research", "--id", "R1", "--question", "Does the marmalade cache expire?",
        "--claim", "it does", "--verdict", "THEORETICAL",
    )  # fmt: skip
    run_cli(
        repo, "decision", "add", "--id", "D1", "--title", "Adopt saffron queues",
        "--decision", "use saffron everywhere",
    )  # fmt: skip
    run_cli(
        repo, "lesson", "add", "--id", "L1", "--title", "Never trust turmeric input",
        "--rule", "validate turmeric first",
    )  # fmt: skip
    run_cli(repo, "session", "prompt", "--text", f"please fix the paprika bug {SECRET}")
    run_cli(repo, "session", "note", "--text", "tried cardamom, a dead end")


def _search(repo, *argv, agent=""):
    code, out, err = run_cli(repo, "--json", "search", *argv, agent=agent)
    body = json.loads(out) if out.strip().startswith("{") else {}
    return code, body, err


def _hit(body, kind, id_=None):
    return [
        r for r in body.get("rows", []) if r["kind"] == kind and (id_ is None or r["id"] == id_)
    ]


def test_each_source_is_found(repo):
    _fixture(repo)
    for word, kind, id_ in (
        ("zirconium", "task", "T1"),
        ("quarry", "phase", "P1"),
        ("xylophone", "bug", "B1"),
        ("marmalade", "research", "R1"),
        ("saffron", "decision", "D1"),
        ("turmeric", "lesson", "L1"),
        ("cardamom", "session", None),
        ("paprika", "prompt", None),
    ):
        code, body, err = _search(repo, word)
        assert code == 0, (word, err)
        assert _hit(body, kind, id_), (word, body)


def test_the_log_is_searched_and_can_be_selected(repo):
    _fixture(repo)
    code, body, _ = _search(repo, "task.added", "--exact", "--kind", "log")
    assert code == 0
    assert body["rows"] and {r["kind"] for r in body["rows"]} == {"log"}
    # a gate event is log-only; a record's own creation event is not repeated by default
    _, body, _ = _search(repo, "zirconium")
    assert not _hit(body, "log")


def test_filters_narrow(repo):
    _fixture(repo)
    _, body, _ = _search(repo, "zirconium")
    assert {r["id"] for r in _hit(body, "task")} == {"T1", "T2"}
    _, body, _ = _search(repo, "zirconium", "--phase", "P1")
    assert [r["id"] for r in body["rows"]] == ["T1"]
    _, body, _ = _search(repo, "zirconium", "--kind", "bug")
    assert body["rows"] == []
    _, body, _ = _search(repo, "zirconium", "--state", "done")
    assert body["rows"] == []
    code, body, _ = _search(repo, "zirconium", "--since", "2999-01-01")
    assert code == 2 and body["rows"] == []
    code, body, _ = _search(repo, "zirconium", "--limit", "1")
    assert body["shown"] == 1 and body["total"] == 2 and body["truncated"] is True


def test_owner_filter_is_the_session_agent(repo):
    run_cli(repo, "init")
    run_cli(repo, "session", "note", "--text", "fennel remark", agent="alice")
    _, body, _ = _search(repo, "fennel", "--owner", "alice")
    assert body["rows"]
    code, body, _ = _search(repo, "fennel", "--owner", "bob")
    assert code == 2 and body["rows"] == []


def test_exact_is_a_substring_and_regex_a_pattern(repo):
    _fixture(repo)
    code, body, _ = _search(repo, "zircon", "--exact")
    assert code == 0 and _hit(body, "task", "T1")
    code, body, _ = _search(repo, "zircon", "--regex")
    assert code == 0 and _hit(body, "task", "T1")
    code, body, _ = _search(repo, r"zirc\w+ium widget", "--regex")
    assert [r["id"] for r in body["rows"]] == ["T1"]
    # ranked mode needs a whole word, exact does not
    code, body, _ = _search(repo, "zircon")
    assert code == 2


def test_snippet_contains_the_match(repo):
    _fixture(repo)
    _, body, _ = _search(repo, "grind", "--exact")
    assert "grind" in body["rows"][0]["snippet"]


def test_pathological_regex_is_refused(repo):
    _fixture(repo)
    for bad in (
        "(a+)+$", "(a|aa)+$", r"(a*)*b", r"(\w+)\1", "x" * 300, "(a{1,2})+b", "(?>a+)+b", "(a|aa){1,1000}b",
        ".*a.*b.*c",
    ):  # fmt: skip
        t0 = time.monotonic()
        code, out, err = run_cli(repo, "search", bad, "--regex")
        assert code == 3, (bad, out, err)
        assert "regex" in err and time.monotonic() - t0 < 30
    code, _, err = run_cli(repo, "search", "(unclosed", "--regex")
    assert code == 3 and "compile" in err


def test_a_secret_in_a_prompt_never_appears(repo):
    _fixture(repo)
    for argv in (("paprika",), ("paprika", "--exact"), ("paprika", "--regex"), (SECRET, "--exact")):
        code, out, err = run_cli(repo, "--json", "search", *argv)
        assert SECRET not in out + err, argv
    code, out, _ = run_cli(repo, "search", "paprika")
    assert code == 0 and "paprika" in out and SECRET not in out
    # and not through the log source either
    code, out, _ = run_cli(repo, "search", "abcdefghijklmnop", "--exact", "--kind", "log,prompt")
    assert SECRET not in out


def test_empty_result_says_so(repo):
    _fixture(repo)
    code, out, _ = run_cli(repo, "search", "nonexistentword")
    assert code == 2 and "No matches" in out


def test_bad_input_is_refused(repo):
    _fixture(repo)
    assert run_cli(repo, "search", "zirconium", "--kind", "nope")[0] == 3
    assert run_cli(repo, "search", "zirconium", "--phase", "NOPE")[0] == 3
    assert run_cli(repo, "search", "zirconium", "--limit", "0")[0] == 3
    assert run_cli(repo, "search", "zirconium", "--since", "garbage")[0] == 3
    assert run_cli(repo, "search", "   ")[0] == 3
    assert run_cli(repo, "search", "the of and")[0] == 3  # only stop words in ranked mode


def test_safe_regexes_still_run_and_odd_syntax_never_crashes(repo):
    _fixture(repo)
    for ok in (r"(zir){1}conium", r"(?:grind|edges)", r"(\d{3})+|zirconium", r"(?=zirc)zirconium"):
        code, _, err = run_cli(repo, "search", ok, "--regex")
        assert code == 0 and "Traceback" not in err, (ok, err)
    for odd in ("(?>zirc)onium", "(?<=a)b", "a{2}{3}", "(?i:ZIRC)onium", "[[:alpha:]]"):
        code, _, err = run_cli(repo, "search", odd, "--regex")
        assert code in (0, 2, 3) and "Traceback" not in err, (odd, err)
