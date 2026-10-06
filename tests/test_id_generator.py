"""One id service mints every id (B-id-generator, decision D-id-schemes-final).

With no `[ids]` section every path mints exactly today's shapes; a configured template
changes what is minted; `{seq}` keys sit over internal ids minted as ever; taken ids get
a suffix; and a ratchet keeps any module from building an id by hand.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import knowledge as K
from ddflow.config import Config
from ddflow.core import ids
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

ROOT = Path(__file__).resolve().parents[1]
HEX = "[0-9a-f]{10}"


def _init(repo: Path, toml: str = "") -> None:
    assert run_cli(repo, "init")[0] == 0
    if toml:
        path = repo / ".ddflow" / "config.toml"
        path.write_text(path.read_text("utf-8") + "\n" + toml, "utf-8")


def _events(repo: Path, kind: str) -> list:
    return [e for e in EventLog(repo, "t").read_all() if e.kind == kind]


# -- golden: today's shapes ------------------------------------------------------------


def test_every_record_kind_mints_todays_shape(repo) -> None:
    _init(repo)
    les = K.lesson_add(repo, K.LessonDraft(title="a rule", rule="do it", why="w", how="h"))
    assert re.fullmatch(f"L{HEX}", les.data["id"]), les.data
    bug = K.bug_found(repo, summary="it broke", no_task=True)
    assert re.fullmatch(f"B{HEX}", bug.data["id"]), bug.data
    mem = K.memory_add(repo, "this box has many cores")
    assert re.fullmatch(f"M{HEX}", mem.data["id"]), mem.data
    code, out, err = run_cli(
        repo, "--json", "decision", "add", "--title", "pick x", "--decision", "use x"
    )
    assert code == 0, err
    assert re.fullmatch(f"D{HEX}", json.loads(out)["id"])
    code, out, err = run_cli(
        repo, "--json", "research", "--question", "q?", "--claim", "c", "--verdict",
        "THEORETICAL", "--falsifier", "f",
    )  # fmt: skip
    assert code == 0, err
    assert re.fullmatch(f"R{HEX}", json.loads(out)["id"])
    code, out, err = run_cli(repo, "--json", "session", "start")
    assert code == 0, err
    assert re.fullmatch(r"s\d{8}T\d{6}-\d+", json.loads(out)["session"])
    # no event carries a key when the key is the id
    for e in EventLog(repo, "t").read_all():
        assert "key" not in e.data, e


def test_a_bugs_fix_task_and_its_follow_up_keep_todays_ids(repo) -> None:
    _init(repo)
    bug = K.bug_found(repo, summary="the widget fails")
    bid = bug.data["id"]
    assert bug.data["fix_task"] == f"fix-{bid}"
    assert run_cli(repo, "abandon", f"fix-{bid}", "--reason", "wrong approach")[0] == 0
    code, out, err = run_cli(repo, "--json", "bug", "file-tasks")
    assert code == 0, err
    assert f"fix-{bid}-2" in out, out


def test_a_split_names_children_as_today(repo) -> None:
    _init(repo)
    run_cli(repo, "task", "add", "P.T", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "split", "P.T", "--into", "=one", "--into", "=two")
    assert code == 0, err
    assert json.loads(out)["created"] == ["P.T.1", "P.T.2"]


def test_the_ci_bug_id_is_todays(repo) -> None:
    import hashlib

    from ddflow.services import ci as CI

    check = "tests/test_x.py::test_y[a-b]"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", check).strip("-").lower()[:30].strip("-")
    old = f"Bci-{slug}-{hashlib.sha1(check.encode()).hexdigest()[:10]}"
    assert CI.bug_id(check) == old
    assert CI.refile_id(old, "abcdef1234") == old + "-abcdef1"
    assert CI.is_filing_of(old + "-abcdef1", old) and not CI.is_filing_of("Bx", old)


def test_a_promotion_id_is_todays() -> None:
    assert ids.make(Config(), "promotion", env="staging", seq=3).id == "promote-staging-3"


# -- configured templates --------------------------------------------------------------


def test_a_seq_key_over_an_internal_id(repo) -> None:
    _init(repo, '[ids]\nbug = "BUG-{seq}"\n')
    first = K.bug_found(repo, summary="first failure", no_task=True)
    K.bug_found(repo, summary="second failure", no_task=True)
    assert re.fullmatch(f"B{HEX}", first.data["id"])  # the internal id, minted as ever
    keys = [e.data.get("key") for e in _events(repo, "bug.found")]
    assert keys == ["BUG-1", "BUG-2"]


def test_a_number_is_never_reused(repo) -> None:
    used = {"BUG-1": "key", "BUG-2": "key", "BUG-9": "key"}
    cfg = Config()
    cfg.ids.bug = "BUG-{seq}"
    assert ids.make(cfg, "bug", used=used, hash_parts=("x",)).key == "BUG-10"


def test_a_template_without_seq_is_the_id(repo) -> None:
    _init(repo, '[ids]\nlesson = "LES-{time}-{pid}"\n')
    out = K.lesson_add(repo, K.LessonDraft(title="r", rule="x", why="w", how="h"))
    assert re.fullmatch(r"LES-\d{8}T\d{6}-\d+", out.data["id"]), out.data


def test_a_taken_id_gets_a_suffix() -> None:
    cfg = Config()
    cfg.ids.lesson = "LES-{time}-{pid}"
    first = ids.make(cfg, "lesson", time=0, pid=7)
    again = ids.make(cfg, "lesson", used={first.id: "lesson"}, time=0, pid=7)
    third = ids.make(cfg, "lesson", used={first.id: "lesson", again.id: "lesson"}, time=0, pid=7)
    assert (again.id, third.id) == (first.id + "-2", first.id + "-3")


def test_a_stable_id_extends_its_own_kind_and_refuses_another() -> None:
    fields = {"slug": "x", "digest": "0123456789"}
    same = ids.make(Config(), "ci_bug", used={"Bci-x-0123456789": "bug"}, **fields)
    assert same.id == "Bci-x-0123456789"  # the same failing check: the same bug
    with pytest.raises(ValueError, match="already taken by a item"):
        ids.make(Config(), "ci_bug", used={"Bci-x-0123456789": "item"}, **fields)


def test_a_configured_fix_task_template_is_used_and_read_back(repo) -> None:
    from ddflow.services.completion import fixes_of

    _init(repo, '[ids]\nfix_task = "bugfix-{parent}"\n')
    bug = K.bug_found(repo, summary="the gadget fails")
    bid = bug.data["id"]
    assert bug.data["fix_task"] == f"bugfix-{bid}"
    cfg = Config.load(repo)
    st = fold(EventLog(repo, "t").read_all(), strict=False)
    assert bid in ids.bugs_named_by_fix_task(cfg, f"bugfix-{bid}")
    assert bid in fixes_of(st, f"bugfix-{bid}", cfg)


def test_a_hand_filed_default_fix_task_still_names_its_bug(repo) -> None:
    from ddflow.services.completion import fixes_of

    _init(repo)
    bid = K.bug_found(repo, summary="the thing fails", no_task=True).data["id"]
    run_cli(repo, "task", "add", f"fix-{bid}", "--globs", "a.py")
    st = fold(EventLog(repo, "t").read_all(), strict=False)
    assert fixes_of(st, f"fix-{bid}") == {bid}


def test_the_session_template_is_honoured(repo) -> None:
    _init(repo, '[ids]\nsession = "sess-{time}-{pid}"\n')
    code, out, err = run_cli(repo, "--json", "session", "start")
    assert code == 0, err
    assert json.loads(out)["session"].startswith("sess-")


# -- the ratchet -----------------------------------------------------------------------

#: Ways a module could mint an id itself. Only the id service may.
HAND_BUILT = re.compile(
    r"\bauto_id\("
    r"|f\"(?:fix|promote)-\{"
    r"|\"(?:fix|promote)-(?:\{|%|\"\s*\+)"
    r"|\"(?:fix|promote)-\"\s*\+"
    r"|\"Bci-"
    r"|strftime\(\"s%Y"
    r"|startswith\(\"(?:fix|promote|Bci)-"
    r"|FIX_TASK_PREFIX|_FIX_PREFIX"
    r"|\bf\"\{item\}\.\{i\}\""
)
SERVICE = {"core/ids.py", "config_sections/ids.py"}


def test_no_module_builds_an_id_by_hand() -> None:
    offenders = []
    for path in sorted((ROOT / "ddflow").rglob("*.py")):
        rel = path.relative_to(ROOT / "ddflow").as_posix()
        if rel in SERVICE:
            continue
        for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if HAND_BUILT.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"ddflow/{rel}:{n}: {line.strip()}")
    assert not offenders, "mint ids through ddflow.core.ids:\n  " + "\n  ".join(offenders)


def test_the_ratchet_can_see() -> None:
    for planted in (
        'x = auto_id("B", s)',
        'tid = f"fix-{bug}"',
        'p = f"promote-{env}-{n}"',
        'b = "Bci-" + slug',
        'sid = time.strftime("s%Y%m%d")',
        'if i.startswith("fix-"):',
        'if i.startswith("promote-"):',
        "tid = FIX_TASK_PREFIX + bug",
        'tid = "fix-" + bug',
        'p = "promote-%s-%d" % (env, n)',
        'p = "fix-{}".format(bug)',
        'sub = f"{item}.{i}"',
    ):
        assert HAND_BUILT.search(planted), planted


def test_the_cli_runs_in_a_git_repo(repo) -> None:
    # sanity for the helpers above: the fixture is a real repository
    assert subprocess.run(["git", "-C", str(repo), "rev-parse"], check=False).returncode == 0


def test_a_follow_up_fix_task_names_its_bug(repo) -> None:
    from ddflow.services.completion import fixes_of

    _init(repo)
    bid = K.bug_found(repo, summary="the sprocket fails", no_task=True).data["id"]
    run_cli(repo, "task", "add", f"fix-{bid}-2", "--globs", "a.py")
    st = fold(EventLog(repo, "t").read_all(), strict=False)
    assert fixes_of(st, f"fix-{bid}-2") == {bid}


def test_a_refiling_is_told_apart_from_another_stable_id() -> None:
    base = "Bci-abc-0123456789"
    assert ids.is_filing_of(base, base)
    assert ids.is_filing_of(ids.refile(base, "deadbeefcafe"), base)
    assert not ids.is_filing_of(base + "-abc1234-ffffffffff", base)  # another check's id


def test_a_key_taken_meanwhile_is_minted_afresh() -> None:
    cfg = Config()
    cfg.ids.bug = "BUG-{seq}"
    first = ids.make(cfg, "bug", used={}, hash_parts=("x",))
    assert first.key == "BUG-1"
    again = ids.confirm(cfg, "bug", first, used=lambda: {"BUG-1": "key"}, hash_parts=("x",))
    assert (again.id, again.key) == (first.id, "BUG-2")
    plain = ids.make(Config(), "bug", hash_parts=("x",))
    calls = []
    assert ids.confirm(Config(), "bug", plain, used=lambda: calls.append(1) or {}) == plain
    assert calls == []  # no key to check: the log is not even read


def test_only_record_events_put_keys_in_the_namespace() -> None:
    from ddflow.core.events import Event

    trig = Event(kind="trigger.fired", subject="T1", data={"key": "BUG-5"}, agent="a", ts="")
    rec = Event(kind="bug.found", subject="B1", data={"key": "BUG-3"}, agent="a", ts="")
    used = ids.taken(None, [trig, rec])
    assert "BUG-3" in used and "BUG-5" not in used


def test_a_seq_in_a_rendered_template_is_allocated() -> None:
    cfg = Config()
    cfg.ids.fix_task = "fix-{parent}-{seq}"
    assert ids.render(cfg, "fix_task", parent="B1") == "fix-B1-1"
    assert ids.render(cfg, "fix_task", used={"fix-B1-1": "item"}, parent="B1") == "fix-B1-2"


def test_a_short_sha_refiling_round_trips() -> None:
    for sha in ("a1b2", "", "deadbeefcafe"):
        assert ids.is_filing_of(ids.refile("Bci-x-0123456789", sha), "Bci-x-0123456789")


def test_only_a_seven_digit_suffix_is_a_refiling_of_a_stable_id() -> None:
    base = ids.make(Config(), "ci_bug", slug="x", digest="0123456789").id
    assert ids.is_filing_of(base + "-abc1234", base)
    for other in (base + "-abc12", base + "-abc12345", base + "-ffffffffff", base + "x"):
        assert not ids.is_filing_of(other, base), other


def test_a_repeated_parent_token_reads_back() -> None:
    cfg = Config()
    cfg.ids.fix_task = "{parent}-fix-{parent}"
    assert ids.bugs_named_by_fix_task(cfg, "B1-fix-B1")[0] == "B1"
    assert "B1" not in ids.bugs_named_by_fix_task(cfg, "B1-fix-B2")


def test_a_sequence_numbered_id_is_never_a_refiling() -> None:
    for rid, base in (
        ("promote-prod-1234567", "promote-prod"),
        ("fix-B1-1234567", "fix-B1"),
        ("promote-prod-1234", "promote-prod"),
    ):
        assert not ids.is_filing_of(rid, base), rid
