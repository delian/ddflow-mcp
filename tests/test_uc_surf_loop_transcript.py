"""The work-loop CLI verbs (next, claim, heartbeat, release, wait, block, unblock, abandon,
remove, gate, complete, merge): the whole transcript (exit code, stdout, stderr) of one
scripted session, pinned before the verbs moved onto the declarative executor
(B-uc-surf-loop). Only the volatile parts (timestamps, hashes, the repo path, elapsed
seconds) are normalised."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from conftest import append_config, run_cli, write_config

from tests.pin_norm import rerecord, stable

ME = "u5pin"
OTHER = "other"

#: A step is an argv run as ME, ``("@name", *argv)`` run as ``name``, or
#: ``("!commit", item, filename)`` which commits a new file in the item's worktree.
SCRIPT: tuple[tuple[str, ...], ...] = (
    ("task", "add", "T1", "--title", "one", "--globs", "a/*"),
    ("task", "add", "T2", "--title", "two", "--needs", "T1", "--globs", "b/*"),
    ("task", "add", "T3", "--title", "three", "--globs", "c/*"),
    ("task", "add", "T4", "--title", "four", "--globs", "d/*", "--tags", "tier:fast"),
    ("task", "add", "T5", "--title", "five", "--globs", "e/*"),
    ("task", "add", "T6", "--title", "six", "--globs", "f/*"),
    ("task", "add", "T7", "--title", "seven", "--globs", "g/*"),
    ("task", "add", "T8", "--title", "eight"),
    ("next",),
    ("--json", "next"),
    ("next", "--kind", "phase"),
    ("next", "--phase", "nowhere"),
    ("--json", "next", "--phase", "nowhere"),
    ("claim", "T1", "--note", "first"),
    ("--json", "claim", "T3", "--no-worktree"),
    ("@" + OTHER, "claim", "T1", "--no-worktree"),
    ("@" + OTHER, "--json", "claim", "T1", "--no-worktree"),
    ("claim", "T2", "--no-worktree"),
    ("claim", "nope"),
    ("--json", "claim", "nope"),
    ("claim", "T4", "--globs", "d/*,d2/*", "--no-worktree"),
    ("heartbeat", "T1"),
    ("--json", "heartbeat", "T1"),
    ("heartbeat", "T5"),
    ("--json", "heartbeat", "T5"),
    ("heartbeat", "nope"),
    ("@" + OTHER, "heartbeat", "T1"),
    ("release", "T3"),
    ("--json", "release", "T3"),
    ("release", "T3"),
    ("release", "nope"),
    ("wait", "--item", "T1", "--timeout", "1", "--poll", "1"),
    ("wait", "--item", "T3", "--timeout", "1", "--poll", "1"),
    ("--json", "wait", "--item", "T3", "--timeout", "1", "--poll", "1"),
    ("--json", "wait", "--item", "T1", "--timeout", "1", "--poll", "1"),
    ("block", "T5", "--reason", "waiting on ops"),
    ("--json", "block", "T6", "--reason", "needs a decision"),
    ("block", "T5", "--reason", "again"),
    ("block", "nope", "--reason", "x"),
    ("unblock", "T5"),
    ("--json", "unblock", "T6", "--note", "decided"),
    ("unblock", "T5"),
    ("--json", "unblock", "T5"),
    ("unblock", "nope"),
    ("abandon", "T4", "--reason", "not needed"),
    ("--json", "abandon", "T7", "--reason", "dropped"),
    ("abandon", "T4", "--reason", "again"),
    ("abandon", "nope", "--reason", "x"),
    ("remove", "T8"),
    ("--json", "remove", "T6", "--reason", "gone"),
    ("remove", "T8"),
    ("remove", "nope"),
    ("gate", "status", "T1"),
    ("--json", "gate", "status", "T1"),
    ("gate", "status", "nope"),
    ("gate", "list"),
    ("--json", "gate", "list"),
    ("gate", "list", "--item", "T1"),
    ("gate", "record", "T1", "research", "--outcome", "passed", "--evidence", "probe ran"),
    ("--json", "gate", "record", "T1", "rules", "--outcome", "passed", "--evidence", "read"),
    ("gate", "record", "T1", "nogate", "--outcome", "passed"),
    ("gate", "record", "T1", "implement", "--outcome", "maybe"),
    ("gate", "skip", "T1", "bug_hunt", "--reason", "nothing to hunt"),
    ("--json", "gate", "skip", "T1", "dedupe", "--reason", "no duplicates"),
    ("gate", "skip", "T1", "standards"),
    ("gate", "verify", "T1", "research"),
    ("--json", "gate", "verify", "T1", "research"),
    ("gate", "verify", "T1", "nogate"),
    ("gate", "run", "T1", "implement"),
    ("gate", "status", "T1"),
    ("complete", "T1"),
    ("--json", "complete", "T1"),
    ("complete", "nope"),
    ("abandon", "T1", "--reason", "not now"),
    ("claim", "T3", "--globs", "c/*"),
    ("!commit", "T3", "c.txt"),
    ("merge", "T3"),
    ("--json", "merge", "T3"),
    ("merge", "nope"),
    ("claim", "T5"),
    ("merge", "T5"),
    ("!dirty", "T5", "wip.txt"),
    ("merge", "T5"),
    ("--json", "merge", "T5"),
    ("merge", "T5", "--allow-dirty", "--allow-empty"),
    ("claim", "T6"),
    ("claim", "T2"),
    ("next",),
    ("--json", "next"),
    ("brief", "--item", "T2"),
    ("--json", "brief", "--item", "T2"),
    ("brief", "--phase", "nowhere"),
    ("--json", "brief", "--phase", "nowhere"),
    ("complete", "T3", "--force", "--model", "claude-opus-5", "--sha", "abc1234"),
    ("task", "add", "T9", "--title", "nine", "--globs", "h/*"),
    ("claim", "T9"),
    ("!commit", "T9", "h.txt"),
    ("--json", "merge", "T9", "--keep"),
    ("--json", "complete", "T9", "--force", "--model", "claude-opus-5"),
    ("complete", "T9"),
    ("brief",),
    ("--json", "brief", "--check-recovery"),
    ("task", "add", "S1", "--title", "splittable", "--globs", "s/*"),
    ("task", "add", "S2", "--title", "also splittable", "--globs", "t/*"),
    ("split", "S1", "--into", "S1a=first", "--into", "S1b"),
    ("--json", "split", "S2", "--into", "S2a=one", "--into", "S2b=two", "--globs", "t/a/*"),
    ("split", "S1", "--into", "S1c=again", "--into", "S1d=twice"),
    ("split", "S2"),
    ("split", "nope", "--into", "a", "--into", "b"),
    ("resolve", "S1", "--keep", "someone"),
    ("--json", "resolve", "S1", "--keep", "someone"),
    ("resolve", "nope", "--keep", "x"),
    ("pr", "status"),
    ("--json", "pr", "status"),
    ("pr", "sync"),
    ("--json", "pr", "sync"),
    ("pr", "sync", "--item", "nope"),
    ("pr", "threads", "S1"),
    ("--json", "pr", "threads", "S1"),
    ("pr", "threads", "nope"),
    ("version", "show"),
    ("--json", "version", "show"),
    ("version", "show", "--bump", "minor", "--line", "nowhere"),
    ("version", "lint"),
    ("--json", "version", "lint"),
    ("version", "lint", "--waive", "knob:x"),
    ("version", "cut", "--dry-run"),
    ("--json", "version", "cut", "--dry-run"),
    ("version", "cut", "--version", "bad"),
    ("flow", "show"),
    ("--json", "flow", "show"),
    ("flow", "choose", "port_strategy", "cherry-pick", "--reason", "pinned"),
    ("--json", "flow", "choose", "model", "trunk"),
    ("flow", "choose", "nope", "y"),
    ("flow", "choose", "model", "nope"),
    ("flow", "show"),
    ("promote", "status"),
    ("--json", "promote", "status"),
    ("promote", "add", "staging"),
    ("promote", "deployed", "staging"),
    ("!append", '[flow]\nenvironments = ["staging"]\n'),
    ("promote", "status"),
    ("--json", "promote", "status"),
    ("promote", "add", "staging"),
    ("promote", "add", "staging", "--force"),
    ("--json", "promote", "add", "staging"),
    ("promote", "deployed", "staging"),
    ("--json", "promote", "deployed", "staging", "--sha", "abc"),
    ("promote", "status"),
    ("!git", "branch", "staging", "main"),
    ("promote", "status"),
    ("promote", "add", "staging"),
    ("promote", "add", "staging", "--force"),
    ("promote", "add", "staging"),
    ("promote", "deployed", "staging"),
    ("--json", "promote", "deployed", "staging"),
    ("promote", "status"),
    ("--json", "promote", "status"),
    ("bug", "found", "--id", "Bx", "--summary", "first problem", "--no-task"),
    ("bug", "found", "--id", "By", "--summary", "second one", "--no-task"),
    ("bug", "reopen", "Bx", "--reason", "early"),
    ("bug", "invalid", "Bx", "--reason", "false", "--evidence", "probe"),
    ("bug", "reopen", "Bx", "--reason", "wrong"),
    ("--json", "bug", "reopen", "Bx", "--reason", "again"),
    ("bug", "invalid", "By", "--reason", "false too"),
    ("--json", "bug", "reopen", "By", "--reason", "regress"),
    ("bug", "reopen", "Bnope", "--reason", "x"),
    ("bug", "reopen", "Bx"),
)


def normalise(text: str, repo: Path) -> str:
    text = text.replace(str(repo), "<repo>")
    text = re.sub(r"\d{4}-\d\d-\d\dT[\d:.+Z-]+", "<time>", text)
    text = re.sub(r'"user": "[^"]*"', '"user": "<user>"', text)
    text = re.sub(
        r'"(at|waited_s|waiting_s|ts|expires|acquired_at|approx_tokens)": [\d.]+',
        r'"\1": <n>',
        text,
    )
    # How long a waiter has waited, before the plain seconds rule takes the "30s" out of "1m 30s".
    text = re.sub(r"\b\d+[ms]( \d+s)? so far", "<n> so far", text)
    text = re.sub(r"(after |)\b\d+(\.\d+)?s\b", r"\1<n>s", text)
    # Every abbreviated or full commit id: a hash of digits only is one as well, so no
    # run in ten fails on a hash that happens to have no letter in it.
    return re.sub(r"\b[0-9a-f]{7,40}\b", "<hex>", text)


def _worktree(repo: Path, item: str) -> Path:
    return repo / ".ddflow" / "worktrees" / item


def transcript(repo: Path) -> list[tuple[tuple[str, ...], int, str, str]]:
    out = []
    # The parallelism limit adapts to the machine's load; a fixed one keeps the pin stable.
    err, _ = write_config(repo, [("schedule.parallel", "fixed")])
    assert not err, err
    for argv in SCRIPT:
        if argv[0] == "!commit":
            wt = _worktree(repo, argv[1])
            (wt / argv[2]).write_text(f"{argv[1]}\n")
            subprocess.run(["git", "-C", str(wt), "add", argv[2]], check=True)
            subprocess.run(["git", "-C", str(wt), "commit", "-qm", f"add {argv[2]}"], check=True)
            continue
        if argv[0] == "!git":
            subprocess.run(["git", "-C", str(repo), *argv[1:]], check=True)
            continue
        if argv[0] == "!append":
            err, _ = append_config(repo, argv[1])
            assert not err, err
            continue
        if argv[0] == "!dirty":
            (_worktree(repo, argv[1]) / argv[2]).write_text("wip\n")
            continue
        who, words = (argv[0][1:], argv[1:]) if argv[0].startswith("@") else (ME, argv)
        code, so, se = run_cli(repo, *words, agent=who)
        out.append((words, code, normalise(so, repo), normalise(se, repo)))
    return out


EXPECTED = Path(__file__).with_suffix(".json")


#: ~600 sequential ddflow subprocesses: 110 s alone, and past the suite-wide 420 s on a host
#: under heavy load (B-loop-transcript-slow). Its own, generous, bound; a hang still ends.
@pytest.mark.timeout(1800)
def test_the_loop_cli_transcript_is_unchanged(repo):
    """The recorded transcript is `test_uc_surf_loop_transcript.json`, re-recorded on purpose
    only, by `ddflow pins regenerate` (which sets DDFLOW_UPDATE_GOLDEN=1): the rows that differ
    are replaced and every other row is kept byte for byte."""
    got = [[list(w), c, o, e] for (w, c, o, e) in transcript(repo)]
    want = json.loads(EXPECTED.read_text("utf-8"))
    if os.environ.get("DDFLOW_UPDATE_GOLDEN"):
        merged = rerecord(got, want)
        if merged != want:
            EXPECTED.write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n", "utf-8")
            want = merged
    assert len(got) == len(want)
    for g, e in zip(got, want, strict=True):
        assert stable(g) == stable(e), f"{g[0]}:\n got {g[1:]}\nwant {e[1:]}"
