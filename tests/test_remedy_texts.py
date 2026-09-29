"""Advice ddflow prints must name commands that exist and outcomes that happened.

A remedy is read by an agent that will run it verbatim. `ddflow lease release T1`
exits 2 with 'invalid choice', and an agent told to run it concludes the tool is
broken; a note saying the critic "never ran" when it reviewed half the diff erases the
half it did. Both are the help-page rot `test_help` already guards against, in text
that `test_help` never read.
"""

from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_help import _cli_leaves, unknown_cli_mentions

from ddflow.core.model import fold
from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog
from ddflow.services import completion as CM
from ddflow.services import gates as G
from ddflow.services import help as H
from ddflow.services import leases as L

PACKAGE = Path(__file__).resolve().parents[1] / "ddflow"

#: Offenders already filed as bugs, outside this item's files. Listed so the ratchet
#: can go in now; delete an entry when its bug is fixed.
KNOWN_OPEN = {
    ("services/companions.py", "companions show"): "B67ba6899c7",
    ("services/gates.py", "item update"): "B84b48b9f71",
}


def _unresolved(text: str) -> list[str]:
    """CLI commands `text` names in backticks that argparse would reject."""
    return unknown_cli_mentions([m for sep, m in H.command_mentions(text) if sep == " "])


def _seed(log: EventLog, item: str = "T1") -> None:
    log.append("phase.added", "P1", {})
    log.append("task.added", item, {"parent": "P1", "globs": [f"src/{item}/*"]})


# -- B4f9019c0d7: recovery remedies ---------------------------------------------------


def _orphan(repo: Path, cfg, *, keep_tree: bool = True) -> L.Recovery:
    """A worktree the item still points at, with nobody's lease on it."""
    log = EventLog(repo, "a")
    _seed(log)
    wt = W.create(repo, cfg, "T1")
    L.acquire(log, cfg, "T1", holder="ghost", worktree=str(wt.path), branch=wt.branch)
    assert L.release(log, "T1", holder="ghost")
    if not keep_tree:
        W.remove(repo, cfg, wt, force=True)
    [rec] = L.scan(log, cfg, repo)
    assert rec.kind == "orphan_worktree" and fold(log.read_all()).items["T1"].lease is None
    return rec


def test_orphan_remedy_names_real_commands_and_no_release(repo, cfg):
    """The B22 case: lease already null, tree clean and merged. The advice named
    `ddflow worktree remove` and `ddflow lease release` -- neither exists -- and the
    second would release a lease there is none of."""
    rec = _orphan(repo, cfg)
    assert "safe to remove" in rec.advice
    assert not _unresolved(rec.advice), rec.advice
    assert "release" not in rec.advice, f"advised releasing a null lease: {rec.advice}"
    assert "`git worktree remove " in rec.advice


def test_orphan_with_a_vanished_tree_advises_no_release(repo, cfg):
    rec = _orphan(repo, cfg, keep_tree=False)
    assert "nothing to salvage" in rec.advice
    assert not _unresolved(rec.advice), rec.advice
    assert "release" not in rec.advice, rec.advice


def test_orphan_with_work_advises_no_release(repo, cfg):
    log = EventLog(repo, "a")
    _seed(log)
    wt = W.create(repo, cfg, "T1")
    (wt.path / "work.py").write_text("x\n")
    L.acquire(log, cfg, "T1", holder="ghost", worktree=str(wt.path), branch=wt.branch)
    L.release(log, "T1", holder="ghost")
    [rec] = L.scan(log, cfg, repo)
    assert rec.kind == "orphan_worktree" and "INSPECT FIRST" in rec.advice
    assert not _unresolved(rec.advice), rec.advice
    assert "release" not in rec.advice, rec.advice


def test_expired_lease_remedies_name_real_commands(repo, cfg):
    """An expired lease still exists, so releasing it is right -- with the real verb."""
    log = EventLog(repo, "a")
    _seed(log)
    wt = W.create(repo, cfg, "T1")
    L.acquire(log, cfg, "T1", holder="crashed", worktree=str(wt.path), branch=wt.branch)
    [clean] = L.scan(log, cfg, repo, now=time.time() + 10**6)
    assert clean.kind == "expired_lease" and "safe to remove" in clean.advice
    assert not _unresolved(clean.advice), clean.advice
    assert "`ddflow release T1`" in clean.advice

    (wt.path / "work.py").write_text("x\n")
    [dirty] = L.scan(log, cfg, repo, now=time.time() + 10**6)
    assert "INSPECT FIRST" in dirty.advice
    assert not _unresolved(dirty.advice), dirty.advice
    assert "`ddflow release T1 --note salvaged`" in dirty.advice

    W.remove(repo, cfg, wt, force=True)
    [gone] = L.scan(log, cfg, repo, now=time.time() + 10**6)
    assert "nothing to salvage" in gone.advice
    assert not _unresolved(gone.advice), gone.advice
    assert "`ddflow release T1`" in gone.advice


# -- B72dd4dde17: the completion note for a partial reviewer ---------------------------


def test_partial_critic_note_says_partial_with_coverage(repo, cfg):
    """A critic that reviewed 3 of 6 chunks RAN. Saying it "never ran" misstates the
    log and hides the half that was reviewed."""
    log = EventLog(repo, "a")
    _seed(log)
    G.record(
        log,
        cfg,
        "T1",
        "critic",
        "partial",
        reason="3 chunks timed out",
        evidence={"status": "PARTIAL", "coverage": "3/6 chunk(s) reviewed", "findings": 7},
    )
    G.record(log, cfg, "T1", "rubber_duck", "unavailable", reason="endpoint down")
    v = CM.verdict(fold(log.read_all()), cfg, "T1", repo=repo)
    note = v.coverage_note
    assert "critic never ran" not in note and "critic, " not in note, note
    assert "critic" in note and "partial" in note and "3/6 chunk(s) reviewed" in note, note
    # A gate that really did not run is still called that.
    assert "rubber_duck never ran" in note, note
    assert "not as a pass" in note, note
    assert sorted(v.coverage_gaps) == ["critic", "rubber_duck"]


def test_partial_gate_without_coverage_evidence_still_says_partial(repo, cfg):
    """A `partial_exits` gate carries no chunk count; it is still not "never ran"."""
    log = EventLog(repo, "a")
    _seed(log)
    G.record(log, cfg, "T1", "unit_tests", "partial", reason="exit 5 is declared PARTIAL")
    note = CM.verdict(fold(log.read_all()), cfg, "T1", repo=repo).coverage_note
    assert "never ran" not in note and "unit_tests" in note and "partial" in note, note


# -- the ratchet -----------------------------------------------------------------------


def _strings(source: str) -> list[tuple[int, str]]:
    """Every string literal in a module except docstrings, f-strings rejoined.

    An f-string's placeholders become `<x>` so a backticked span split by one --
    `ddflow release {rec.item}` -- is scanned whole. Docstrings are for maintainers
    and are prose; the ratchet is about text a user or agent is told to run.
    """
    tree = ast.parse(source)
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
        if isinstance(node, ast.JoinedStr):
            skip.update(id(v) for v in node.values)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else "<x>" for v in node.values)
        else:
            continue
        # Leading indentation would make `command_mentions` read the whole line as code.
        out.append((node.lineno, "\n".join(ln.strip() for ln in text.splitlines())))
    return out


def test_every_command_printed_advice_names_exists():
    """The class, not the instance: any `ddflow <command>` in backticks in a string
    the package can print must resolve in the real argparse tree. A future remedy
    naming a command that does not exist fails here, the way a help page does in
    `test_help`."""
    leaves = _cli_leaves()
    unknown: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        for lineno, text in _strings(path.read_text("utf-8")):
            mentions = [m for sep, m in H.command_mentions(text) if sep == " "]
            for m in unknown_cli_mentions(mentions, leaves):
                if (rel, m) not in KNOWN_OPEN:
                    unknown.append(f"{rel}:{lineno}: `ddflow {m}`")
    assert not unknown, "printed advice names commands that do not exist:\n" + "\n".join(unknown)


def test_the_ratchet_catches_a_fake_command():
    """The scanner itself: an f-string remedy naming a nonexistent verb is found, and
    a real one with a placeholder is not."""
    src = 'x = f"then `ddflow nosuchcmd {item}` and `ddflow release {item} --note n`"\n'
    [(_, text)] = _strings(src)
    assert _unresolved(text) == ["nosuchcmd"], text
