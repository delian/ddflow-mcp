"""The CLI surfaces of the add-time duplicate check (B-add-dedupe-surfaces, decision
D-no-duplicates): the answer flags, ``--check``, the terminal prompt and the refusal a
script gets. What an answer DOES to the log is tests/test_add_dedupe.py."""

from __future__ import annotations

import json
import os
import pty
import select
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

FIRST = (
    "claim refuses a worktree that already exists on disk instead of adopting the existing worktree"
)
SECOND = "claim refuses an existing worktree on disk rather than adopting the worktree that already exists there"
UNRELATED = "zebra quantum marmalade"

#: argv of every add command, each carrying SECOND in the text that is compared.
ADDS = {
    "task": ["task", "add", "T-new", "--title", SECOND],
    "phase": ["phase", "add", "P-new", "--title", SECOND],
    "bug": ["bug", "found", "--id", "B-new", "--summary", SECOND],
    "lesson": ["lesson", "add", "--id", "L-new", "--title", SECOND, "--rule", SECOND],
    "decision": ["decision", "add", "--id", "D-new", "--title", SECOND, "--decision", SECOND],
    "research": [
        "research",
        "add",
        "--id",
        "R-new",
        "--question",
        SECOND,
        "--verdict",
        "THEORETICAL",
    ],
    "memory": ["memory", "add", SECOND, "--id", "M-new"],
}
#: The command that files FIRST in the same kind, so SECOND meets it.
SEEDS = {
    "task": ["task", "add", "T-old", "--title", FIRST],
    "phase": ["phase", "add", "P-old", "--title", FIRST],
    "bug": ["bug", "found", "--id", "B-old", "--summary", FIRST],
    "lesson": ["lesson", "add", "--id", "L-old", "--title", FIRST, "--rule", FIRST],
    "decision": ["decision", "add", "--id", "D-old", "--title", FIRST, "--decision", FIRST],
    "research": [
        "research",
        "add",
        "--id",
        "R-old",
        "--question",
        FIRST,
        "--verdict",
        "THEORETICAL",
    ],
    "memory": ["memory", "add", FIRST, "--id", "M-old"],
}
OLD = {
    "task": "T-old",
    "phase": "P-old",
    "bug": "B-old",
    "lesson": "L-old",
    "decision": "D-old",
    "research": "R-old",
    "memory": "M-old",
}


@pytest.fixture(autouse=True)
def _ask(monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")


@pytest.fixture
def filed_as(repo, monkeypatch):
    """``filed_as(kind)``: a project in which FIRST is filed as ONE kind of record (the
    check compares across kinds, so seven copies would crowd the candidate list)."""

    def make(kind: str) -> Path:
        run_cli(repo, "init")
        monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "off")
        code, out, err = run_cli(repo, *SEEDS[kind])
        monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
        assert code == 0, (kind, out, err)
        return repo

    return make


def events(repo: Path) -> str:
    return "".join(p.read_text() for p in sorted((repo / ".ddflow" / "events").glob("*.jsonl")))


def state(repo: Path):
    return fold(EventLog(repo, "a").read_all(), strict=False)


@pytest.mark.parametrize("kind", ADDS)
def test_without_a_terminal_a_duplicate_is_refused_with_the_commands_that_answer_it(filed_as, kind):
    filed = filed_as(kind)
    before = events(filed)
    code, out, err = run_cli(filed, *ADDS[kind])
    assert code == 3, (out, err)
    assert "refused: possible duplicate" in err
    assert OLD[kind] in err
    assert "Traceback" not in err and "KeyError" not in err + out, (
        "a refusal renders, it does not crash"
    )
    assert events(filed) == before, "a refusal writes nothing"
    cmds = [ln.strip() for ln in err.splitlines() if ln.strip().startswith("ddflow ")]
    assert [c.rsplit(" ", 1)[-1] if "--new" in c else c.split()[-2] for c in cmds] == [
        "--new",
        "--extends",
        "--duplicate-of",
        "--related",
    ]


@pytest.mark.parametrize("kind", ADDS)
def test_every_command_a_refusal_names_runs(filed_as, kind):
    """The remedy-text rule applied to what the refusal PRINTS: run each one."""
    filed = filed_as(kind)
    _c, _o, err = run_cli(filed, *ADDS[kind])
    cmds = [ln.strip() for ln in err.splitlines() if ln.strip().startswith("ddflow ")]
    assert len(cmds) == 4
    for n, line in enumerate(cmds):
        argv = shlex.split(line)[1:]
        assert argv[:2] == ["--repo", str(filed)], "the command is copy-pasteable as it stands"
        # Each answer on its own copy: two of them file the same id.
        copy = filed.parent / f"copy{n}"
        shutil.copytree(filed, copy)
        code, out, err2 = run_cli(copy, *argv[2:])
        assert code == 0, (line, out, err2)


def test_new_files_it_and_records_the_answer(filed_as):
    filed = filed_as("bug")
    code, _out, err = run_cli(filed, *ADDS["bug"], "--new")
    assert code == 0, err
    assert "B-new" in state(filed).bugs
    assert state(filed).links["B-new"].answer["answer"] == "new"


def test_extends_an_open_record_adds_to_it_and_files_nothing(filed_as):
    filed = filed_as("task")
    code, out, err = run_cli(filed, *ADDS["task"], "--extends", "T-old")
    assert code == 0, err
    assert "T-old" in out and "no new record" in out
    st = state(filed)
    assert "T-new" not in st.items
    assert st.links["T-old"].extensions


def test_related_files_a_linked_record(filed_as):
    filed = filed_as("memory")
    code, _o, err = run_cli(filed, *ADDS["memory"], "--related", "M-old")
    assert code == 0, err
    assert state(filed).links["M-new"].linked("related") == {"M-old"}


def test_json_extension_carries_what_the_check_decided(filed_as):
    filed = filed_as("bug")
    code, out, _e = run_cli(filed, "--json", *ADDS["bug"], "--duplicate-of", "B-old")
    assert code == 0
    body = json.loads(out)
    assert body["extended"] == "B-old" and body["relation"] == "duplicate_of"


def test_the_answer_flags_are_mutually_exclusive(filed_as):
    filed = filed_as("task")
    before = events(filed)
    code, _o, err = run_cli(filed, *ADDS["task"], "--new", "--extends", "T-old")
    assert code == 2 and "not allowed with" in err
    assert events(filed) == before


def test_an_unknown_target_is_a_failure_not_a_write(filed_as):
    filed = filed_as("task")
    before = events(filed)
    code, _o, err = run_cli(filed, *ADDS["task"], "--extends", "T-nope")
    assert code == 1 and "T-nope" in err
    assert events(filed) == before


@pytest.mark.parametrize("kind", ADDS)
def test_check_prints_the_candidates_and_writes_nothing(filed_as, kind):
    filed = filed_as(kind)
    before = events(filed)
    code, out, err = run_cli(filed, *ADDS[kind], "--check")
    assert code == 0, (out, err)
    assert OLD[kind] in out and "REFUSED" in out
    assert events(filed) == before, "--check wrote to the log"


def test_check_with_no_candidates_exits_2(filed_as):
    filed = filed_as("task")
    before = events(filed)
    code, out, _e = run_cli(filed, "task", "add", "T-z", "--title", UNRELATED, "--check")
    assert code == 2 and "no existing record" in out
    assert events(filed) == before


def test_check_json_lists_candidates_and_options(filed_as):
    filed = filed_as("task")
    code, out, _e = run_cli(filed, "--json", *ADDS["task"], "--check")
    body = json.loads(out)
    assert code == 0 and body["would_ask"] is True
    assert body["candidates"][0]["id"] == "T-old"
    assert {"relation": "extends", "target": "T-old"} in body["options"]


def test_every_add_command_documents_the_flags():
    for argv in ADDS.values():
        help_ = subprocess.run(
            [sys.executable, "-m", "ddflow", *argv[:2], "--help"],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        ).stdout
        for flag in ("--new", "--extends", "--duplicate-of", "--related", "--check"):
            assert flag in help_, (argv[:2], flag)


# -- the terminal prompt ---------------------------------------------------------------


def _on_a_terminal(repo: Path, argv: list[str], replies: list[str], timeout: float = 60):
    """Run ddflow with a pseudo-terminal as stdin and stdout, typing each reply when the
    menu appears. Returns (exit code, everything it printed)."""
    master, slave = pty.openpty()
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), *argv],
        stdin=slave,
        stdout=slave,
        stderr=subprocess.PIPE,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    seen, typed, deadline = b"", 0, time.monotonic() + timeout
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                seen += chunk
                while typed < len(replies) and seen.count(b"[a]bort") > typed:
                    os.write(master, (replies[typed] + "\n").encode())
                    typed += 1
        code = proc.wait(timeout=10)
        while select.select([master], [], [], 0.1)[0]:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            seen += chunk
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
        proc.stderr.close()
    return code, seen.decode(errors="replace")


def test_on_a_terminal_a_duplicate_asks_and_extends_on_the_reply(filed_as):
    filed = filed_as("task")
    code, shown = _on_a_terminal(filed, ADDS["task"], ["e 1"])
    assert code == 0, shown
    assert "[n]ew / [e]xtends # / [d]uplicate of # / [r]elated # / [a]bort" in shown
    assert "T-old" in shown
    st = state(filed)
    assert "T-new" not in st.items and st.links["T-old"].extensions


def test_on_a_terminal_new_files_the_record(filed_as):
    filed = filed_as("bug")
    code, shown = _on_a_terminal(filed, ADDS["bug"], ["n"])
    assert code == 0, shown
    assert "B-new" in state(filed).bugs


def test_on_a_terminal_related_by_id(filed_as):
    filed = filed_as("memory")
    code, shown = _on_a_terminal(filed, ADDS["memory"], ["r M-old"])
    assert code == 0, shown
    assert state(filed).links["M-new"].linked("related") == {"M-old"}


def test_on_a_terminal_abort_files_nothing_and_exits_3(filed_as):
    filed = filed_as("task")
    before = events(filed)
    code, shown = _on_a_terminal(filed, ADDS["task"], ["a"])
    assert code == 3, shown
    assert events(filed) == before


def test_on_a_terminal_a_bad_reply_asks_again(filed_as):
    filed = filed_as("task")
    code, shown = _on_a_terminal(filed, ADDS["task"], ["what", "e"])
    assert code == 0, shown
    assert "Not understood" in shown
    assert state(filed).links["T-old"].extensions


def test_a_flag_on_a_terminal_does_not_prompt(filed_as):
    filed = filed_as("task")
    code, shown = _on_a_terminal(filed, [*ADDS["task"], "--new"], [])
    assert code == 0
    assert "[a]bort" not in shown
    assert "T-new" in state(filed).items


def test_the_prompt_reply_parser():
    from ddflow.surfaces.dedupe_flags import parse_reply

    rows = [{"id": "A"}, {"id": "B"}]
    assert parse_reply("n", rows).relation == "new"
    got = parse_reply("d 2", rows)
    assert (got.relation, got.target) == ("duplicate_of", "B")
    assert parse_reply("e a", rows).target == "A"
    assert parse_reply("a", rows) is None
    assert parse_reply("e", rows) is False, "two candidates: which one?"
    assert parse_reply("e", rows[:1]).target == "A"
    assert parse_reply("r 9", rows) is False
    assert parse_reply("e \u00b2", rows) is False, "a superscript is not a number"
    assert parse_reply("", rows) is False


@pytest.mark.parametrize("kind", ADDS)
def test_a_json_refusal_is_parseable_and_carries_candidates(filed_as, kind):
    filed = filed_as(kind)
    code, out, err = run_cli(filed, "--json", *ADDS[kind])
    assert code == 3 and "Traceback" not in err
    body = json.loads(out)
    assert body["candidates"][0]["id"] == OLD[kind]
    assert {"relation": "new", "target": ""} in body["options"]


def test_the_commands_quote_a_hostile_candidate_id():
    """A record id is nearly free text; the line printed as ready to paste must not run it."""
    from ddflow.surfaces.dedupe_flags import commands

    for line in commands(["task", "add", "T1"], [{"id": "X; rm -rf ~"}]):
        assert shlex.split(line)[-1] in ("--new", "X; rm -rf ~"), line
        assert "; rm" not in line.replace("'X; rm -rf ~'", "")


def test_check_says_an_exact_copy_is_recorded_without_asking(filed_as):
    filed = filed_as("task")
    code, out, _e = run_cli(filed, "task", "add", "T-copy", "--title", FIRST, "--check")
    assert code == 0 and "exact copy" in out and "T-old" in out
    code, out, _e = run_cli(filed, "--json", "task", "add", "T-copy", "--title", FIRST, "--check")
    assert json.loads(out)["would_extend"] == "T-old"


def test_check_names_the_knob_when_the_kind_is_not_checked(filed_as):
    filed = filed_as("task")
    (filed / ".ddflow" / "config.toml").write_text('[dedupe]\nkinds = ["bug"]\n')
    code, out, _e = run_cli(filed, *ADDS["task"], "--check")
    assert code == 2 and "[dedupe].kinds" in out and "on_match = off" not in out


def test_check_is_a_failure_not_a_clean_bill_when_the_index_cannot_be_read(filed_as, monkeypatch):
    """An outage must not read as "nothing like it" (exit 2) to a caller that gates on it."""
    from ddflow import api as A
    from ddflow.api import _dedupe as DD

    filed = filed_as("task")
    real = DD._assess

    def broken(repo, log, cfg, st, rec):
        found, _shown, _why = real(repo, log, cfg, st, rec)
        return found, [], "OSError: index is locked"

    monkeypatch.setattr(DD, "_assess", broken)
    out = A.task_add(filed, "T-new", title=SECOND, answer=DD.Answer(check_only=True), agent="a")
    assert out.exit == 1 and "could not run" in out.reason
    assert out.data["dedupe_unavailable"] == "OSError: index is locked"


def test_the_commands_put_the_flag_before_an_end_of_options_separator():
    from ddflow.surfaces.dedupe_flags import commands

    got = commands(["task", "add", "T1", "--", "-retry storm"], [{"id": "T-old"}])
    assert got[1] == "ddflow task add T1 --extends T-old -- '-retry storm'"
    assert got[0] == "ddflow task add T1 --new -- '-retry storm'"
