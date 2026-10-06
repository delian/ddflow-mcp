"""Every add path runs the duplicate check, or says why not (B-coh-coverage, D-no-duplicates).

The add-time check (`api._dedupe.check_add`) only protects the paths that call it. These
tests are the RATCHET that keeps it so:

* every event kind the fold knows is classified: an ADD that is checked (and by which API
  function), or exempt with a reason. A new kind fails here until somebody decides;
* every function that appends a checked kind runs `check_add` itself, or is listed with
  the reason it may not (a split, a generated fix task, the importer's own check);
* every CLI verb and MCP tool that records text is classified the same way;
* each checked kind has a behavioural refusal test (`test_add_dedupe_cli.ADDS`).

And the gaps the audit found, each with its regression test: `bug fixed --lesson-title`
filed a lesson unchecked (B4ea345b51e); every title-only rule read as a duplicate of every
other (Bd4c9bcb87e); `rule add --related` was advertised and failed (B3be768717c).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_add_dedupe_cli import ADDS

from ddflow.api import rules as R
from ddflow.core.model import GATE_OUTCOMES, HANDLERS, fold
from ddflow.infra.log import EventLog
from ddflow.surfaces import cli
from ddflow.surfaces.tools import TOOLS

PKG = Path(__file__).resolve().parents[1] / "ddflow"

#: Event kinds that FILE a record whose text may repeat one already filed -> the API add
#: function that runs the check for it.
CHECKED: dict[str, str] = {
    "task.added": "items.task_add",
    "phase.added": "items.phase_add",
    "bug.found": "knowledge.bug_found",
    "lesson.recorded": "knowledge.lesson_add",
    "decision.recorded": "decisions.decision_add",
    "research.recorded": "knowledge.research_add",
    "memory.recorded": "knowledge.memory_add",
}

_STATE = "a state change of a record that already exists; it adds no text to compare"
_UPDATE = "changes or closes an existing record (same id); the add was checked"
_OBSERVED = "a machine observation or run record, not authored text"
_CONFIG = "who changed a setting or approved a tool; the setting itself lives in config"
#: Every other kind, and why it is not duplicate-checked. The gate records of an item
#: (`GATE_RECORDS`, state) are exempt by name below.
EXEMPT: dict[str, str] = {
    **dict.fromkeys(
        (
            "lease.acquired",
            "lease.renewed",
            "lease.released",
            "lease.expired",
            "item.started",
            "item.blocked",
            "item.unblocked",
            "item.completed",
            "item.reopened",
            "item.abandoned",
            "item.resolved",
            "phase.removed",
            "task.removed",
            "review.triaged",
            "worktree.created",
            "worktree.adopted",
            "worktree.merged",
            "worktree.removed",
            "pr.opened",
            "pr.synced",
            "pr.changes_requested",
            "pr.merged",
            "pr.closed",
            "port.applied",
            "backmerge.recorded",
            "deploy.recorded",
            "release.opened",
            "release.tagged",
            "release.closed",
            "session.started",
            "session.ended",
            "gate.out_of_order",
        ),
        _STATE,
    ),
    **dict.fromkeys(
        (
            "phase.updated",
            "task.updated",
            "bug.fixed",
            "bug.invalid",
            "bug.reopened",
            "bug.reported_upstream",
            "decision.superseded",
            "memory.forgotten",
        ),
        _UPDATE,
    ),
    **dict.fromkeys(
        ("ci.result", "external.observed", "job.started", "job.ended", "cadence.ran"), _OBSERVED
    ),
    **dict.fromkeys(
        (
            "flow.chosen",
            "reviewer.configured",
            "reviewer.approved",
            "export.enabled",
            "export.disabled",
            "export.acknowledged",
            "ddflow.seen",
            "skew.overridden",
            "upgrade.applied",
        ),
        _CONFIG,
    ),
    "session.prompt": "the operator's words, verbatim: never refused or merged (D-no-duplicates)",
    "session.note": "a journal entry of what happened, verbatim (D-no-duplicates)",
    **dict.fromkeys(
        ("schedule.defined", "schedule.updated", "schedule.removed"),
        "a scheduled job's definition: a document keyed by its id (defining an id again "
        "replaces it) and validated by api.schedule; the authoring surfaces that add a "
        "duplicate check are B-sched-author",
    ),
    **dict.fromkeys(
        ("trigger.evaluated", "trigger.fired", "trigger.suppressed"),
        "the trigger evaluator's own record of a run, a fire or a suppression: no authored text",
    ),
    "record.extended": "the duplicate check's own answer: text added onto a checked record",
    "link.recorded": "the duplicate check's own answer: two records judged related or the same",
}

#: Functions that append a CHECKED kind without calling `check_add` themselves, and why.
#: `items.resolve` writes `<kind>.added` through a computed kind (re-recording the rival
#: add a contest kept), so it is not a literal and not listed.
WRITERS_EXEMPT: dict[str, str] = {
    "api/items.py:split": "the parts of an item being split: their text is the parent's, "
    "which every part would match",
    "api/knowledge.py:_file_fix_task": "the fix task of a bug that was itself just checked",
    "api/knowledge.py:link_record": "merges a lesson INTO the one it duplicates: the answer "
    "to a duplicate, not a new record",
    "api/knowledge.py:bug_file_tasks": "attaches a fix task to an existing bug (same id)",
    "api/bug_reopen.py:refile_reported": "attaches a fix task to an existing bug (same id)",
    "api/schedule.py:_apply": "a trigger's remediation item, generated from its job; "
    "de-duplicated by the trigger's dedupe key (one open remediation per key)",
    "services/promotions.py:add": "a generated 'Promote X to Y' task; one open promotion per "
    "environment is enforced instead",
    "services/importer.py:apply_import": "the plan was de-duplicated against the queue with "
    "the same engine and [dedupe] thresholds (`_dedupe_found`) before apply",
    "services/importer_harness.py:apply": "each fact is checked with similar.assess / "
    "first_duplicate before it is written",
}

#: CLI verbs (as argv words) that record text: -> "checked" | "own check: ..." | a reason.
CLI_VERBS: dict[tuple[str, ...], str] = {
    ("task", "add"): "checked",
    ("phase", "add"): "checked",
    ("bug", "found"): "checked",
    ("lesson", "add"): "checked",
    ("decision", "add"): "checked",
    ("research",): "checked",
    ("memory", "add"): "checked",
    ("rule", "add"): "own check: rules are files, not log records, and are compared with "
    "each other by api.rules.rule_dedup_check",
    ("split",): WRITERS_EXEMPT["api/items.py:split"],
    ("import",): WRITERS_EXEMPT["services/importer.py:apply_import"],
    ("promote", "add"): WRITERS_EXEMPT["services/promotions.py:add"],
    ("job", "add"): "a long-running process an item waits on, not authored text",
    ("session", "prompt"): EXEMPT["session.prompt"],
    ("session", "note"): EXEMPT["session.note"],
    ("hooks", "prompt"): "the harness hook that records the operator's prompt: "
    + EXEMPT["session.prompt"],
    ("reviewers", "add"): _CONFIG,
    ("companions", "add"): _CONFIG,
    ("prompts", "eject"): "copies the shipped prompt templates verbatim; there is no "
    "prompt-template add",
}
_TEXT_VERB = re.compile(r"^(add|found|research|import|split|note|prompt|eject|define|propose)$")

#: MCP tools that record text, the same way. A checked tool must pass the `relation` answer.
MCP_TOOLS: dict[str, str] = {
    "ddflow_task_add": "checked",
    "ddflow_phase_add": "checked",
    "ddflow_bug_found": "checked",
    "ddflow_lesson_add": "checked",
    "ddflow_decision_add": "checked",
    "ddflow_research_add": "checked",
    "ddflow_memory_add": "checked",
    "ddflow_rule_add": CLI_VERBS[("rule", "add")],
    "ddflow_split": CLI_VERBS[("split",)],
    "ddflow_import": CLI_VERBS[("import",)],
    "ddflow_promote_add": CLI_VERBS[("promote", "add")],
    "ddflow_job_add": CLI_VERBS[("job", "add")],
    "ddflow_session_prompt": EXEMPT["session.prompt"],
    "ddflow_session_note": EXEMPT["session.note"],
    "ddflow_companions_add": _CONFIG,
}
_TEXT_TOOL = re.compile(r"_(add|found|import|split|note|prompt|define|propose)$")


# -- the ratchets --------------------------------------------------------------------------


#: The gate records of an item: `gate.started` and one kind per recorded outcome.
GATE_RECORDS = {f"gate.{o}" for o in ("started", *GATE_OUTCOMES)}


def test_every_event_kind_is_checked_or_exempt_with_a_reason():
    """Only the gate RECORD kinds are exempt as a family, by name; any other `gate.*`
    kind is classified like the rest (rubber-duck on B-coh-coverage)."""
    assert "gate.out_of_order" not in GATE_RECORDS  # a pipeline note, classified below
    kinds = set(HANDLERS) - GATE_RECORDS
    unclassified = sorted(kinds - set(CHECKED) - set(EXEMPT))
    assert not unclassified, (
        f"new event kind(s) {unclassified}: does each FILE a record whose text could repeat "
        f"one already filed? Then its add path runs api._dedupe.check_add (CHECKED); "
        f"otherwise say why not in EXEMPT"
    )
    stale = sorted((set(CHECKED) | set(EXEMPT)) - kinds)
    assert not stale, f"classified kinds the fold no longer knows: {stale}"
    assert not set(CHECKED) & set(EXEMPT)
    assert all(len(r) > 20 for r in EXEMPT.values())


def _appenders() -> dict[str, set[str]]:
    """function ("path:name") -> the CHECKED kinds it appends as a literal, with whether
    its own body calls check_add."""
    out: dict[str, set[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        tree = ast.parse(path.read_text("utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for call in ast.walk(fn):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "append"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value in CHECKED
                ):
                    out.setdefault(f"{rel}:{fn.name}", set()).add(call.args[0].value)
    return out


def _calls_check_add(key: str) -> bool:
    rel, name = key.split(":")
    tree = ast.parse((PKG / rel).read_text("utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
    )
    return any(
        isinstance(c, ast.Call)
        and (
            (isinstance(c.func, ast.Attribute) and c.func.attr == "check_add")
            or (isinstance(c.func, ast.Name) and c.func.id == "check_add")
        )
        for c in ast.walk(fn)
    )


def test_every_writer_of_a_checked_kind_runs_the_check_or_says_why_not():
    writers = _appenders()
    unchecked = sorted(k for k in writers if not _calls_check_add(k) and k not in WRITERS_EXEMPT)
    assert not unchecked, (
        f"{unchecked} append {sorted(set().union(*(writers[k] for k in unchecked)))} without "
        f"api._dedupe.check_add: run it, or add the function to WRITERS_EXEMPT with the reason"
    )
    stale = sorted(k for k in WRITERS_EXEMPT if k not in writers or _calls_check_add(k))
    assert not stale, f"WRITERS_EXEMPT names functions that no longer need it: {stale}"


def test_the_ratchet_can_fail():
    """The scan finds a known unchecked writer, and the check-call detector a known one."""
    writers = _appenders()
    assert "services/promotions.py:add" in writers
    assert not _calls_check_add("services/promotions.py:add")
    assert _calls_check_add("api/knowledge.py:memory_add")


def _cli_leaves() -> list[tuple[str, ...]]:
    def walk(p, path):
        subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs:
            yield path
            return
        for name, sp in subs[0].choices.items():
            yield from walk(sp, (*path, name))

    return list(walk(cli.build_parser(), ()))


def test_every_cli_verb_that_records_text_is_classified():
    text = {leaf for leaf in _cli_leaves() if any(_TEXT_VERB.match(w) for w in leaf)}
    missing = sorted(text - set(CLI_VERBS))
    assert not missing, f"CLI verbs that record text, unclassified: {missing}"
    stale = sorted(set(CLI_VERBS) - set(_cli_leaves()))
    assert not stale, f"classified CLI verbs that no longer exist: {stale}"


def _tool_api_source(name: str) -> str:
    for path in sorted((PKG / "surfaces" / "tools").glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for k, v in zip(node.keys, node.values, strict=False):
                if isinstance(k, ast.Constant) and k.value == name and isinstance(v, ast.Dict):
                    for kk, vv in zip(v.keys, v.values, strict=False):
                        if isinstance(kk, ast.Constant) and kk.value == "api":
                            return ast.unparse(vv)
    return ""


def test_every_mcp_tool_that_records_text_is_classified_and_a_checked_one_takes_the_answer():
    text = {t for t in TOOLS if _TEXT_TOOL.search(t)}
    missing = sorted(text - set(MCP_TOOLS))
    assert not missing, f"MCP tools that record text, unclassified: {missing}"
    assert not set(MCP_TOOLS) - set(TOOLS), "classified tools that no longer exist"
    for name, how in MCP_TOOLS.items():
        if how == "checked":
            assert "_answer(a)" in _tool_api_source(name), (
                f"{name} records a checked kind but does not pass the duplicate-check "
                f"answer (`relation`)"
            )


def test_every_checked_kind_has_a_behavioural_refusal_test():
    """`test_add_dedupe_cli` files a near-copy through each add command and expects the
    refusal; its table must cover exactly the checked kinds."""
    assert {k.split(".")[0] for k in CHECKED} == set(ADDS)


# -- the gaps the audit closed -------------------------------------------------------------


@pytest.fixture
def proj(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # conftest turns the duplicate check off for every other test; these test it.
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
    code, out, err = run_cli(repo, "init")
    assert code == 0, (code, out, err)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    return repo


LESSON = "Always assert the exit code of every setup step a test runs before it"


def _fixed(proj: Path, title: str, bug_text: str) -> tuple[int, str, str]:
    code, out, err = run_cli(proj, "bug", "found", "--summary", bug_text, "--no-task", "--new")
    assert code == 0, (code, out, err)
    bid = re.search(r"bug (B\w+) recorded", out).group(1)  # type: ignore[union-attr]
    return run_cli(
        proj,
        "bug",
        "fixed",
        bid,
        "--regression-test",
        "tests/test_x.py",
        "--skip-regression-verify",
        "--verify-reason",
        "test",
        "--lesson-title",
        title,
        "--lesson-rule",
        "assert each setup step",
    )


def _lessons(proj: Path) -> dict:
    return fold(EventLog(proj, "t").read_all()).lessons


def test_a_lesson_captured_by_bug_fixed_that_copies_one_extends_it(proj):
    """B4ea345b51e: the identical lesson was filed a second time."""
    code, out, err = run_cli(
        proj,
        "lesson",
        "add",
        "--id",
        "L-old",
        "--title",
        LESSON,
        "--rule",
        "assert each setup step",
    )
    assert code == 0, (code, out, err)
    code, out, err = _fixed(proj, LESSON, "the helper ignored a failing git init in setup")
    assert code == 0, (code, out, err)
    assert set(_lessons(proj)) == {"L-old"}
    assert "lesson added to L-old, which it copies" in out
    # `lesson_captured` names the lesson that holds the text, merged or not (rubber-duck)
    from ddflow.api import knowledge as K

    code, out, err = run_cli(proj, "bug", "found", "--summary", "another setup exit", "--no-task")
    bid = re.search(r"bug (B\w+) recorded", out).group(1)  # type: ignore[union-attr]
    res = K.bug_fixed(
        proj,
        bid,
        regression_test="tests/test_x.py",
        lesson_title=LESSON,
        lesson_rule="assert each setup step",
        verify_regression=False,
        verify_reason="t",
    )
    assert res.data["lesson_captured"] == "L-old"


def test_a_lesson_captured_by_bug_fixed_that_reads_like_one_is_filed_linked(proj):
    code, out, err = run_cli(
        proj,
        "lesson",
        "add",
        "--id",
        "L-old",
        "--title",
        LESSON,
        "--rule",
        "assert each setup step",
    )
    assert code == 0, (code, out, err)
    close = "Always assert the exit code of each setup step a test runs before its body"
    code, out, err = _fixed(proj, close, "the helper ignored a failing git init in setup")
    assert code == 0, (code, out, err)
    st = fold(EventLog(proj, "t").read_all())
    [new] = [i for i in st.lessons if i != "L-old"]
    assert st.links[new].linked("related") == {"L-old"}
    assert st.links[new].answer.get("auto") is True
    assert f"lesson {new} captured, linked to L-old" in out


def test_a_capture_whose_check_could_not_run_says_so(proj, monkeypatch):
    """roborev on B-coh-coverage: an unavailable check must never read as a clean capture."""
    from ddflow.api import _dedupe as DD
    from ddflow.api import knowledge as K
    from ddflow.surfaces.commands.knowledge import _lesson_tail

    real = DD._assess

    def broken(repo, log, cfg, st, rec):
        found, _shown, _why = real(repo, log, cfg, st, rec)
        return found, [], "OSError: index is locked"

    code, out, err = run_cli(proj, "bug", "found", "--summary", "setup exit ignored", "--no-task")
    assert code == 0, (code, out, err)
    bid = re.search(r"bug (B\w+) recorded", out).group(1)  # type: ignore[union-attr]
    monkeypatch.setattr(DD, "_assess", broken)
    res = K.bug_fixed(
        proj,
        bid,
        regression_test="tests/test_x.py",
        lesson_title=LESSON,
        lesson_rule="r",
        verify_regression=False,
        verify_reason="t",
    )
    assert res.exit == 0, res.reason
    cap = res.data["lesson_capture"]
    assert cap["dedupe_unavailable"] == "OSError: index is locked"
    assert res.data["lesson_captured"] == cap["captured"] == f"L-{bid}"
    assert "filed UNCHECKED" in _lesson_tail(cap)


def test_a_new_lesson_captured_by_bug_fixed_is_filed_as_before(proj):
    code, out, err = _fixed(proj, LESSON, "the helper ignored a failing git init in setup")
    assert code == 0, (code, out, err)
    [new] = [i for i in _lessons(proj) if i.startswith("L-B")]
    assert f"lesson {new} captured" in out


def test_the_capture_reaches_json_and_mcp(proj):
    """roborev on B-coh-coverage: the capture keys were projected away on both surfaces."""
    from ddflow.surfaces.tools import TOOLS as T

    assert {"lesson_captured", "lesson_capture"} <= set(T["ddflow_bug_fixed"]["payload"])
    code, out, err = run_cli(
        proj,
        "lesson",
        "add",
        "--id",
        "L-old",
        "--title",
        LESSON,
        "--rule",
        "assert each setup step",
    )
    assert code == 0, (code, out, err)
    code, out, err = run_cli(proj, "bug", "found", "--summary", "setup exit ignored", "--no-task")
    bid = re.search(r"bug (B\w+) recorded", out).group(1)  # type: ignore[union-attr]
    code, out, err = run_cli(
        proj,
        "--json",
        "bug",
        "fixed",
        bid,
        "--regression-test",
        "tests/test_x.py",
        "--skip-regression-verify",
        "--verify-reason",
        "t",
        "--lesson-title",
        LESSON,
        "--lesson-rule",
        "assert each setup step",
    )
    assert code == 0, (code, out, err)
    body = json.loads(out)
    assert body["lesson_capture"]["extended"] == "L-old"
    assert body["lesson_captured"] == "L-old"

    assert body["lesson_capture"]["candidates"][0]["id"] == "L-old"


def test_two_title_only_rules_are_compared_by_their_titles(proj):
    """Bd4c9bcb87e: two empty contents scored 1.0, so the second was always refused."""
    one = R.rule_add(proj, R.Rule(id="r-one", title="Name tests after the behaviour", content=""))
    assert one.exit == 0, one
    two = R.rule_add(proj, R.Rule(id="r-two", title="Use TOML for every config file", content=""))
    assert two.exit == 0, two.reason
    three = R.rule_add(
        proj, R.Rule(id="r-three", title="Name tests after their behaviour", content="")
    )
    assert three.exit == 3 and "r-one" in three.reason
    # the dry run each surface calls compares what the add compares (roborev)
    assert R.rule_dedup_check_dry_run(proj, "", title="Pin every dependency version").exit == 2
    code, out, err = run_cli(
        proj, "rule", "add", "--id", "r-four", "--title", "Pin every dependency version", "--check"
    )
    assert code == 2, (code, out, err)


def test_a_rule_answered_related_is_filed_and_says_so(proj):
    """B3be768717c: `--related` was offered and failed as an unknown relation."""
    text = "Always claim the item before you edit any file in this repository"
    assert R.rule_add(proj, R.Rule(id="r-claim", title="Claim first", content=text)).exit == 0
    near = R.Rule(id="r-claim-2", title="Claim first", content=text + " please")
    assert R.rule_add(proj, near).exit == 3
    code, out_, err = run_cli(
        proj,
        "rule",
        "add",
        "--id",
        "r-claim-2",
        "--title",
        "Claim first",
        "--content",
        text + " please",
        "--related",
        "r-claim",
    )
    assert code == 0 and "added rule r-claim-2 (related to r-claim)" in out_, (code, out_, err)
    out = R.rule_add(
        proj,
        R.Rule(id="r-claim-4", title="Claim first", content=text + " always"),
        dedup_answer=R.RuleDedupAnswer("related", "r-claim"),
    )
    assert out.exit == 0, out.reason
    assert out.body(("id", "candidates", "related"))["related"] == "r-claim"
    missing = R.rule_add(
        proj,
        R.Rule(id="r-claim-3", title="Claim first", content=text + " now"),
        dedup_answer=R.RuleDedupAnswer("related", "r-nope"),
    )
    assert missing.exit == 1 and "not found" in missing.reason
