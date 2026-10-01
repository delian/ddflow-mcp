"""Advice ddflow prints must name commands that exist and outcomes that happened.

A remedy is read by an agent that will run it verbatim. `ddflow lease release T1`
exits 2 with 'invalid choice', and an agent told to run it concludes the tool is
broken; a note saying the critic "never ran" when it reviewed half the diff erases the
half it did. Both are the help-page rot `test_help` already guards against, in text
that `test_help` never read.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_help import _cli_leaves, mentions, unknown_cli_mentions, unknown_mentions

from ddflow.core.model import fold
from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog
from ddflow.services import completion as CM
from ddflow.services import gates as G
from ddflow.services import leases as L

PACKAGE = Path(__file__).resolve().parents[1] / "ddflow"

#: Offenders already filed as bugs, outside this item's files. Keyed by
#: `(path under ddflow/, mention as unknown_mentions reports it, snippet)`, where the
#: snippet is text from the very string (in code) or line (in a template) that holds
#: the mention. The snippet is what makes an entry approve ONE occurrence: keyed by
#: file and mention alone, an entry written for one sentence also passed any later,
#: unrelated bad mention in that file that reduced to the same word.
#: SHRINK-ONLY: an entry must match exactly one occurrence -- none means the bug was
#: fixed and the line must go; several means the snippet is too loose to say which.
KNOWN_OPEN: dict[tuple[str, str, str], str] = {}

#: Not defects: PROSE that `command_mentions` reads as code because a markdown list
#: continuation is indented ("...that is what ddflow writes."). Same key, same
#: exactly-one rule.
PROSE = {
    ("templates/drivers/deltas/antigravity.md", "writes", "rules. ddflow writes `AGENTS.md`."),
    ("templates/drivers/deltas/devin.md", "writes", "and that is what ddflow writes. **Cloud"),
    ("templates/drivers/deltas/qodo.md", "writes", "what ddflow writes. The **Qodo Gen** IDE"),
    ("templates/drivers/deltas/zcode-glm.md", "writes", "so `AGENTS.md` is what ddflow writes."),
    ("templates/drivers/implement-phase.md", "can tell", "on every review so ddflow can tell."),
    (
        "templates/prompts/commands/code-clean.md",
        "worktree and branch",
        "ddflow_cleanup     — every ddflow worktree and branch, classified",
    ),
}

ALLOWED = {**dict.fromkeys(PROSE, "prose"), **KNOWN_OPEN}


def _unresolved(text: str) -> list[str]:
    """CLI commands `text` names in backticks that argparse would reject."""
    return unknown_cli_mentions([m for sep, m in mentions(text) if sep == " "])


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


def test_a_zero_coverage_figure_is_shown_and_a_missing_one_is_named(repo, cfg):
    """0 is a figure: a reviewer that covered nothing must say so, not fall back to a
    bare "ran only partially". Absent evidence says "coverage unknown"."""
    log = EventLog(repo, "a")
    _seed(log)
    G.record(log, cfg, "T1", "critic", "partial", reason="r", evidence={"coverage": 0})
    G.record(log, cfg, "T1", "rubber_duck", "partial", reason="r", evidence={"coverage": ""})
    note = CM.verdict(fold(log.read_all()), cfg, "T1", repo=repo).coverage_note
    assert "critic ran only partially (coverage: 0)" in note, note
    assert "rubber_duck ran only partially (coverage unknown)" in note, note


# -- B930f5c6b7c: an adopted tree is the harness's, never ours to remove -----------------


def _adopted(repo: Path, cfg, *, released: bool, work: str = "") -> L.Recovery:
    """What `claim` records from inside a tree the harness made (test_worktree_adoption):
    `worktree.adopted`, then a lease on that tree. Clean and fully merged unless `work`
    is "dirty" (an uncommitted file) or "unmerged" (a commit not on main)."""
    agent_tree = repo.parent / "agent-tree"

    def git(where: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)

    git(repo, "worktree", "add", "-q", str(agent_tree), "-b", "agent-work")
    if work:
        (agent_tree / "a.py").write_text("the agent's work\n")
    if work == "unmerged":
        git(agent_tree, "add", "a.py")
        git(agent_tree, "commit", "-qm", "the agent's work")
    log = EventLog(repo, "a")
    _seed(log)
    stored = W.store_path(repo, agent_tree)
    log.append("worktree.adopted", "T1", {"path": stored, "branch": "agent-work", "base": ""})
    L.acquire(log, cfg, "T1", holder="harness", worktree=stored, branch="agent-work")
    if released:
        L.release(log, "T1", holder="harness")
    assert fold(log.read_all()).items["T1"].adopted
    [rec] = L.scan(log, cfg, repo, now=time.time() + 10**6)
    assert rec.salvageable is bool(work), rec
    return rec


def test_an_adopted_tree_past_its_lease_is_never_advised_for_removal(repo, cfg):
    """787962a turned an inert remedy into a working one: `git worktree remove` on the
    harness session's own live directory -- the tree `merge` refuses to delete."""
    rec = _adopted(repo, cfg, released=False)
    assert rec.kind == "expired_lease"
    assert "remove" not in rec.advice and "safe to" not in rec.advice, rec.advice
    assert "adopted" in rec.advice, rec.advice
    assert "`ddflow release T1`" in rec.advice, rec.advice
    assert not _unresolved(rec.advice), rec.advice


def test_an_adopted_orphan_is_never_advised_for_removal_or_release(repo, cfg):
    rec = _adopted(repo, cfg, released=True)
    assert rec.kind == "orphan_worktree"
    assert "remove" not in rec.advice and "safe to" not in rec.advice, rec.advice
    assert "release" not in rec.advice and "adopted" in rec.advice, rec.advice


@pytest.mark.parametrize("work", ["dirty", "unmerged"])
@pytest.mark.parametrize("released", [False, True])
def test_an_adopted_tree_with_work_is_framed_as_the_harness_tree(repo, cfg, work, released):
    """Salvageable work in an adopted tree: inspect and salvage, yes -- but the advice
    must say whose tree it is, and never suggest removing it."""
    rec = _adopted(repo, cfg, released=released, work=work)
    assert "INSPECT FIRST" in rec.advice, rec.advice
    assert "adopted" in rec.advice and "harness" in rec.advice, rec.advice
    assert "remove" not in rec.advice and "safe to" not in rec.advice, rec.advice
    assert ("`ddflow release T1 --note salvaged`" in rec.advice) is not released, rec.advice
    assert not _unresolved(rec.advice), rec.advice


# -- the ratchet -----------------------------------------------------------------------


def _text(node: ast.AST) -> str | None:
    """A string expression's text, or None. f-string placeholders become `<x>` so a
    backticked span split by one -- `ddflow release {rec.item}` -- is scanned whole;
    `"a " + f"b {x}"` is joined the same way. A `.format()` template is scanned as its
    literal, and what it formats in is not: a documented limit."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "<x>" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left), _text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _strings(source: str) -> list[tuple[int, str]]:
    """Every string expression in a module except docstrings, outermost only.

    Docstrings are for maintainers and are prose; the ratchet is about text a user or
    agent is told to run. The parts of a joined expression are skipped, or a span cut
    in two by `+` would be read as two halves after being read whole.
    """
    tree = ast.parse(source)
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        text = _text(node)
        if text is None:
            continue
        skip.update(id(n) for n in ast.walk(node) if n is not node)
        # Leading indentation would make `command_mentions` read the whole line as code.
        out.append((node.lineno, "\n".join(ln.strip() for ln in text.splitlines())))
    return out


#: One unresolved mention: (path under ddflow/, mention, where, the text holding it).
Occurrence = tuple[str, str, str, str]


def _occurrences(rel: str, source: str, leaves: set[str]) -> list[Occurrence]:
    """Unresolved mentions in one file, each with the string or line that holds it."""
    out: list[Occurrence] = []
    if rel.endswith(".py"):
        for lineno, text in _strings(source):
            for m in unknown_mentions(mentions(text), leaves):
                out.append((rel, m, f"{rel}:{lineno}", text))
        return out
    # Markdown: `command_mentions` must see the whole page (a fenced block changes what
    # counts as code), so a line's mentions are what the page up to it adds -- its
    # results only ever extend as lines are appended.
    lines = source.splitlines()
    seen = 0
    for i, line in enumerate(lines):
        upto = mentions("\n".join(lines[: i + 1]))
        for m in unknown_mentions(upto[seen:], leaves):
            out.append((rel, m, f"{rel}:{i + 1}", line.strip()))
        seen = len(upto)
    return out


def _scan() -> list[Occurrence]:
    """Every unresolved mention in printed strings and shipped templates."""
    leaves = _cli_leaves()
    files = sorted(PACKAGE.rglob("*.py"))
    # Templates are rendered to agents verbatim -- the MCP instructions, the command
    # prompts, the drivers. `test_help` reads the help pages; this reads the rest too.
    files += sorted((PACKAGE / "templates").rglob("*.md"))
    out: list[Occurrence] = []
    for path in files:
        out += _occurrences(path.relative_to(PACKAGE).as_posix(), path.read_text("utf-8"), leaves)
    return out


def _judge(found: list[Occurrence], allowed) -> tuple[list[str], list[str]]:
    """(occurrences no entry approves, entries that do not approve exactly one)."""
    hits = {key: [o for o in found if o[:2] == key[:2] and key[2] in o[3]] for key in allowed}
    approved = {id(o) for matched in hits.values() if len(matched) == 1 for o in matched}
    unknown = [f"{o[2]}: `{o[1]}`" for o in found if id(o) not in approved]
    bad = [f"{k} [{allowed[k]}] matches {len(v)}" for k, v in hits.items() if len(v) != 1]
    return unknown, bad


def test_every_command_printed_advice_names_exists():
    """The class, not the instance: any `ddflow <command>` or `ddflow_<tool>` in code
    context in a string the package can print, or in a template it renders, must
    resolve -- in the argparse tree or the MCP tool table. A future remedy naming a
    command that does not exist fails here, the way a help page does in `test_help`."""
    unknown, _ = _judge(_scan(), ALLOWED)
    assert not unknown, "printed advice names commands that do not exist:\n" + "\n".join(unknown)


def test_the_allowlists_only_shrink():
    """Every entry approves exactly one occurrence. None: a fixed bug nobody crossed off
    -- left there, a pass waiting for the next offender at the same spot. Several: the
    snippet no longer says which occurrence it was written for."""
    _, bad = _judge(_scan(), ALLOWED)
    assert not bad, "fix or delete these allowlist entries:\n" + "\n".join(bad)


def test_an_entry_approves_only_the_occurrence_it_names():
    """The loophole a re-review CONFIRMED: keyed by (file, mention), an entry approved
    for one sentence also swallowed a second, unrelated `ddflow writes` in that file."""
    src = 'a = "that is `ddflow writes` here"\n\n\nb = "and later `ddflow writes` again"\n'
    found = _occurrences("fake.py", src, _cli_leaves())
    assert [o[1] for o in found] == ["writes", "writes"], found
    entry = {("fake.py", "writes", "that is `ddflow writes` here"): "prose"}
    unknown, bad = _judge(found, entry)
    assert unknown == ["fake.py:4: `writes`"] and not bad, (unknown, bad)
    # A snippet that fits both approves neither, and is itself reported.
    unknown, bad = _judge(found, {("fake.py", "writes", "`ddflow writes`"): "prose"})
    assert len(unknown) == 2 and len(bad) == 1, (unknown, bad)
    # The same holds per LINE in a template.
    md = "  that is what ddflow writes.\n\n  and so ddflow writes.\n"
    found = _occurrences("fake.md", md, _cli_leaves())
    unknown, _ = _judge(found, {("fake.md", "writes", "that is what ddflow writes."): "p"})
    assert unknown == ["fake.md:3: `writes`"], unknown


def test_the_ratchet_catches_a_fake_command():
    """The scanner itself: an f-string remedy naming a nonexistent verb is found, a
    real one with a placeholder is not, and neither is lost to `+` concatenation."""
    src = 'x = f"then `ddflow nosuchcmd {item}` and `ddflow release {item} --note n`"\n'
    [(_, text)] = _strings(src)
    assert _unresolved(text) == ["nosuchcmd"], text
    joined = 'y = "then `ddflow " + "nosuchcmd` and " + f"`ddflow_nosuchtool {z}`"\n'
    [(_, text)] = _strings(joined)
    assert unknown_mentions(mentions(text)) == ["ddflow_nosuchtool", "nosuchcmd"]


def test_a_refused_registration_carries_the_entry_to_paste(repo):
    """B67ba6899c7: an agent with no project MCP file was told to run `ddflow companions
    show` for the entry -- no such command. The refusal now carries the entry itself."""
    import json

    from conftest import run_cli

    from ddflow.services import companions as CO

    run_cli(repo, "init")
    comp = next(c for c in CO.load(repo) if c.id == "context7")
    status, message = CO.register(repo, comp, "aider")
    assert status == "refused", (status, message)
    assert not _unresolved(message), message
    assert json.dumps(comp.entry()) in message, message
