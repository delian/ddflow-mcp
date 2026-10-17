"""B-rc-lifecycle: history, supersede, merge, split and restore on the one record surface, and
the evidence (session, prompt) every definition write now carries.

The kind is a throwaway one; nothing here is wired into the real CLI.
"""

from __future__ import annotations

import dataclasses

import pytest
from test_record_surface import NOTE, _parser

from ddflow.api import _base
from ddflow.api import record_lifecycle as LC
from ddflow.api import records as R
from ddflow.core import outcome as O
from ddflow.infra.log import EventLog
from ddflow.surfaces import records as S
from ddflow.surfaces.context import Ctx


def _add(repo, rid, title, body="", tags=(), level="low"):
    out = R.record_add(
        repo,
        NOTE,
        rid,
        {"title": title, "body": body, "tags": list(tags), "level": level},
        answer=None,
    )
    assert out.exit == O.OK, out.reason
    return out


@pytest.fixture
def two(repo):
    _add(repo, "n1", "Always use tabs", "tabs are indented with tab characters", ["style", "a"])
    _add(
        repo, "n2", "Deploy on fridays", "release trains leave friday evening", ["ops", "a"], "high"
    )
    return repo


def _me(repo):
    """The log as the writer sees it: the agent id is settled (suffixed) from the second load on."""
    _base._load(repo)[0].append("session.note", "warmup", {"text": "x"})
    return _base._load(repo)[0]


def _events(repo, key):
    log = _base._load(repo)[0]
    return [e for e in log.read_all() if e.subject == key]


# -- history ---------------------------------------------------------------------------


def test_history_is_the_whole_chain_with_before_and_after_and_not_cut_at_twenty(repo):
    _add(repo, "n1", "t", "v0")
    for i in range(1, 26):
        assert R.record_edit(repo, NOTE, "n1", {"body": f"v{i}"}).exit == O.OK
    out = LC.record_history(repo, NOTE, "n1", diff=True)
    assert out.exit == O.OK
    assert out.data["total"] == 26 and len(out.data["entries"]) == 26
    assert R.record_show(repo, NOTE, "n1").data["history_total"] == 26  # show stays cut
    first, second, last = out.data["entries"][0], out.data["entries"][1], out.data["entries"][-1]
    assert first["changes"]["body"] == {"after": "v0"} or first["changes"]["body"]["after"] == "v0"
    assert second["changes"] == {"body": {"before": "v0", "after": "v1"}}
    assert last["changes"] == {"body": {"before": "v24", "after": "v25"}}
    assert [e["version"] for e in out.data["entries"]] == list(range(1, 27))


def test_history_limit_keeps_the_newest_and_says_so(repo):
    _add(repo, "n1", "t", "v0")
    for i in range(1, 5):
        R.record_edit(repo, NOTE, "n1", {"body": f"v{i}"})
    out = LC.record_history(repo, NOTE, "n1", limit=2)
    assert [e["version"] for e in out.data["entries"]] == [4, 5]
    assert out.data["total"] == 5 and out.data["truncated"] is True
    assert LC.record_history(repo, NOTE, "n1", limit=-1).exit == O.REFUSED
    assert LC.record_history(repo, NOTE, "nope").exit == O.FAIL


def test_history_names_who_when_why_and_the_other_side_of_a_supersede(two):
    out = LC.record_supersede(two, NOTE, "n1", by="n2", reason="n2 says it better")
    assert out.exit == O.OK
    h = LC.record_history(two, NOTE, "n1").data["entries"][-1]
    assert h["event"] == "def.superseded" and h["successor"] == "n2"
    assert h["reason"] == "n2 says it better" and h["by"]
    assert R.record_list(two, NOTE).data["total"] == 1  # n1 is out of force, both stay
    assert R.record_show(two, NOTE, "n2").exit == O.OK
    assert (
        LC.record_supersede(two, NOTE, "n2", by="n1", reason="x").exit == O.REFUSED
    )  # n1 not active
    assert LC.record_supersede(two, NOTE, "n2", by="n2", reason="x").exit == O.FAIL
    assert LC.record_supersede(two, NOTE, "n2", by="zz", reason=" ").exit == O.FAIL


# -- evidence --------------------------------------------------------------------------


def test_every_definition_write_carries_the_open_session_and_its_latest_prompt(repo):
    log = _me(repo)
    log.append("session.started", "s1", {})
    log.append("session.prompt", "s1", {"text": "first"})
    last = log.append("session.prompt", "s1", {"text": "second"})
    _add(repo, "n1", "t", "x")
    R.record_edit(repo, NOTE, "n1", {"body": "y"})
    R.record_remove(repo, NOTE, "n1", reason="gone")
    for ev in _events(repo, "skill:n1"):
        assert ev.data["provenance"]["session"] == "s1", ev.kind
        assert ev.data["provenance"]["prompt"] == last.id, ev.kind
    shown = R.record_show(repo, NOTE, "n1").data
    assert shown["history"][0]["provenance"]["session"] == "s1"
    entries = LC.record_history(repo, NOTE, "n1").data["entries"]
    assert {e["session"] for e in entries} == {"s1"}
    assert {e["prompt"] for e in entries} == {last.id}


def test_without_a_session_nothing_is_stamped_and_an_edit_with_no_change_is_still_nothing(repo):
    _add(repo, "n1", "t", "x")
    (ev,) = _events(repo, "skill:n1")
    assert set(ev.data["provenance"]) == {"by"}
    assert R.record_edit(repo, NOTE, "n1", {"body": "x"}).exit == O.NOTHING
    _me(repo).append("session.started", "s2", {})
    assert R.record_edit(repo, NOTE, "n1", {"body": "x"}).exit == O.NOTHING


# -- merge -----------------------------------------------------------------------------


def test_merge_unions_lists_keeps_the_target_scalar_and_points_both_ways(two):
    out = LC.record_merge(two, NOTE, ["n2"], into="n1", reason="same topic", text="One note.")
    assert out.exit == O.OK, out.reason
    n1 = R.record_show(two, NOTE, "n1").data
    assert n1["fields"]["tags"] == ["style", "a", "ops"]
    assert n1["fields"]["level"] == "low" and n1["fields"]["body"] == "One note."
    assert n1["merged_from"] == ["n2"]
    n2 = R.record_show(two, NOTE, "n2").data
    assert n2["status"] == "merged" and n2["successor"] == "n1"
    assert LC.record_history(two, NOTE, "n2").data["entries"][-1]["successor"] == "n1"
    assert LC.record_history(two, NOTE, "n1").data["entries"][-1]["reason"] == "same topic"


def test_merge_take_picks_a_scalar_from_a_source(two):
    out = LC.record_merge(two, NOTE, ["n2"], into="n1", reason="r", take=["level=n2"])
    assert out.exit == O.OK
    assert R.record_show(two, NOTE, "n1").data["fields"]["level"] == "high"
    assert LC.record_merge(two, NOTE, ["n2"], into="n1", reason="r").exit == O.REFUSED  # n2 merged


@pytest.mark.parametrize(
    "ids,into,take,why",
    [
        (["n1"], "n1", None, "none of them the target"),
        (["n2", "n2"], "n1", None, "different records"),
        (["zz"], "n1", None, "no note"),
        (["n2"], "zz", None, "no note"),
        (["n2"], "n1", ["level=zz"], "not among the records"),
        (["n2"], "n1", ["tags=n2"], "union"),
        (["n2"], "n1", ["oops"], "field=id"),
    ],
)
def test_merge_refusals_write_nothing(two, ids, into, take, why):
    before = len(_base._load(two)[0].read_all())
    out = LC.record_merge(two, NOTE, ids, into=into, reason="r", take=take)
    assert out.exit in (O.FAIL, O.REFUSED) and why in out.reason
    assert len(_base._load(two)[0].read_all()) == before


def test_merge_is_one_unit_a_failure_after_the_first_write_leaves_nothing(two, monkeypatch):
    log = _base._load(two)[0]
    before = log.read_all()
    real = EventLog._write
    calls = []

    def flaky(self, kind, subject, data, observed=()):
        calls.append(kind)
        if len(calls) == 2:
            raise OSError("disk went away")
        return real(self, kind, subject, data, observed)

    monkeypatch.setattr(EventLog, "_write", flaky)
    with pytest.raises(OSError):
        LC.record_merge(two, NOTE, ["n2"], into="n1", reason="r", text="x")
    monkeypatch.undo()
    after = _base._load(two)[0].read_all()
    assert [e.id for e in after] == [e.id for e in before]
    assert R.record_show(two, NOTE, "n2").data["status"] == "active"
    assert R.record_show(two, NOTE, "n1").data["fields"]["tags"] == ["style", "a"]
    # and the clock is not left ahead: the next write works and replays the same everywhere
    assert LC.record_merge(two, NOTE, ["n2"], into="n1", reason="r").exit == O.OK


def test_merge_into_a_weaker_record_is_refused_unless_allowed(repo):
    import dataclasses

    def weak(before, after):
        return (
            "level lowered" if before.get("level") == "high" and after.get("level") == "low" else ""
        )

    kind = dataclasses.replace(NOTE, weakening=weak)
    _add(repo, "a", "Alpha policy", "alpha words one", level="low")
    _add(repo, "b", "Beta process", "completely different beta text", level="high")
    out = LC.record_merge(repo, kind, ["b"], into="a", reason="r")
    assert out.exit == O.REFUSED and "weaken" in out.reason and "level lowered" in out.reason
    assert LC.record_merge(repo, kind, ["b"], into="a", reason="r", allow_weaken=True).exit == O.OK


def test_replay_of_a_merge_is_byte_identical(two):
    from ddflow.core.model import fold

    LC.record_merge(two, NOTE, ["n2"], into="n1", reason="r", text="T")
    events = _base._load(two)[0].read_all()
    a, b = fold(events, strict=False), fold(list(events), strict=False)
    assert a.defs == b.defs == _base._load(two)[2].defs
    assert a.defs["skill:n1"].merged_from == ["n2"]


# -- split -----------------------------------------------------------------------------


PARTS = [
    "p1:title=Tabs only,body=indent with tab characters only",
    "p2:title=Spaces never,body=never indent with space characters",
]


def test_split_makes_parts_that_say_where_they_came_from_and_retires_the_original(two):
    out = LC.record_split(two, NOTE, "n1", parts=PARTS, reason="two rules in one")
    assert out.exit == O.OK, out.reason
    assert out.data["parts"] == ["p1", "p2"]
    orig = R.record_show(two, NOTE, "n1").data
    assert orig["status"] == "retired" and orig["history"][-1]["split_into"] == ["p1", "p2"]
    p1 = R.record_show(two, NOTE, "p1").data
    assert p1["status"] == "active"
    assert p1["fields"]["tags"] == ["style", "a"]  # what a part does not say, it inherits
    assert p1["provenance"]["split_from"] == "n1"
    e = LC.record_history(two, NOTE, "p1").data["entries"][0]
    assert e["split_from"] == "n1" and e["reason"] == "two rules in one"


def test_split_refuses_one_part_a_clash_a_non_active_original_and_a_duplicate_part(two):
    one = LC.record_split(two, NOTE, "n1", parts=PARTS[:1], reason="r")
    assert one.exit == O.REFUSED and "two or more" in one.reason
    taken = LC.record_split(two, NOTE, "n1", parts=[PARTS[0], "n2:title=x"], reason="r")
    assert taken.exit == O.REFUSED and "already exists" in taken.reason
    same = LC.record_split(
        two,
        NOTE,
        "n1",
        parts=[
            "p1:title=Tabs only,body=indent with tab characters",
            "p2:title=Tabs only,body=indent with tab characters",
        ],
        reason="r",
    )
    assert same.exit == O.REFUSED and "p2 reads as part p1" in same.reason
    assert R.record_show(two, NOTE, "n1").data["status"] == "active"
    assert R.record_show(two, NOTE, "p1").exit == O.FAIL  # nothing was written
    R.record_remove(two, NOTE, "n1", reason="gone")
    assert LC.record_split(two, NOTE, "n1", parts=PARTS, reason="r").exit == O.REFUSED
    assert (
        LC.record_split(two, NOTE, "n2", parts=["x y:title=a", "z:title=b"], reason="r").exit
        == O.FAIL
    )


def test_split_from_a_toml_file_inside_the_repo(two):
    (two / "parts.toml").write_text(
        '[[part]]\nid = "p1"\ntitle = "Tabs only"\nbody = "indent with tab characters only"\n'
        '[[part]]\nid = "p2"\ntitle = "Spaces never"\nbody = "never indent with space characters"\n'
        'tags = ["x"]\n'
    )
    out = LC.record_split(two, NOTE, "n1", from_file="parts.toml", reason="r")
    assert out.exit == O.OK, out.reason
    assert R.record_show(two, NOTE, "p2").data["fields"]["tags"] == ["x"]
    bad = LC.record_split(two, NOTE, "n2", from_file="../outside.toml", reason="r")
    assert bad.exit == O.FAIL and "inside the repository" in bad.reason


# -- restore ---------------------------------------------------------------------------


def test_restore_returns_the_chosen_version_and_history_says_where_from(repo):
    _add(repo, "n1", "t", "one")
    R.record_edit(repo, NOTE, "n1", {"body": "two"})
    R.record_edit(repo, NOTE, "n1", {"body": "three"})
    assert LC.record_restore(repo, NOTE, "n1", reason="r").exit == O.REFUSED  # active
    R.record_remove(repo, NOTE, "n1", reason="gone")
    out = LC.record_restore(repo, NOTE, "n1", to=1, reason="it was right the first time")
    assert out.exit == O.OK and out.data["was"] == "retired"
    shown = R.record_show(repo, NOTE, "n1").data
    assert shown["status"] == "active" and shown["fields"]["body"] == "one"
    last = LC.record_history(repo, NOTE, "n1").data["entries"][-1]
    assert last["restored_from"] and last["reason"] == "it was right the first time"
    # the default is the last version, and a lamport names an entry as well
    R.record_remove(repo, NOTE, "n1", reason="again")
    assert LC.record_restore(repo, NOTE, "n1", reason="r").exit == O.OK
    assert R.record_show(repo, NOTE, "n1").data["fields"]["body"] == "one"
    entries = LC.record_history(repo, NOTE, "n1").data["entries"]
    second = next(e for e in entries if e.get("version") == 2)
    R.record_remove(repo, NOTE, "n1", reason="again")
    LC.record_restore(repo, NOTE, "n1", to=f"lamport:{second['lamport']}", reason="r")
    assert R.record_show(repo, NOTE, "n1").data["fields"]["body"] == "two"
    R.record_remove(repo, NOTE, "n1", reason="again")
    bad = LC.record_restore(repo, NOTE, "n1", to="lamport:999999", reason="r")
    assert bad.exit == O.REFUSED and "no such version" in bad.reason


def test_restore_of_a_merged_record_leaves_the_absorber_and_of_a_split_says_parts_stay(two):
    LC.record_merge(two, NOTE, ["n2"], into="n1", reason="r")
    assert R.record_show(two, NOTE, "n1").data["merged_from"] == ["n2"]
    assert LC.record_restore(two, NOTE, "n2", reason="not the same after all").exit == O.OK
    assert R.record_show(two, NOTE, "n1").data["merged_from"] == []
    LC.record_split(two, NOTE, "n1", parts=PARTS, reason="r")
    out = LC.record_restore(two, NOTE, "n1", reason="undo")
    assert out.exit == O.OK and out.data["parts_stay"] == ["p1", "p2"]
    assert R.record_show(two, NOTE, "p1").data["status"] == "active"
    assert R.record_show(two, NOTE, "n1").data["status"] == "active"


# -- both surfaces ---------------------------------------------------------------------


def test_every_lifecycle_verb_gives_the_same_body_on_the_cli_and_the_tool(two, capsys):
    tool = S.record_commands(NOTE)[0]

    def cli(*argv):
        a = _parser().parse_args(["--repo", str(two), "--json", *argv])
        code = a.fn(a, Ctx(a))
        return code, capsys.readouterr().out

    def via_tool(**args):
        return tool.call(two, args, "")

    code, out = cli("note", "merge", "n2", "--into", "n1", "--reason", "r", "--text", "T")
    assert code == O.OK and '"merged"' in out
    assert via_tool(verb="restore", id="n2", reason="r").exit == O.OK
    code, out = cli("note", "history", "n1", "--diff", "--limit", "2")
    assert code == O.OK
    t = via_tool(verb="history", id="n1", diff=True, limit=2)
    import json

    assert json.loads(out) == json.loads(json.dumps(t.body()))
    code, _ = cli("note", "split", "n1", "--part", PARTS[0], "--part", PARTS[1], "--reason", "r")
    assert code == O.OK
    code, out = cli("note", "restore", "n1", "--to", "1", "--reason", "r")
    assert code == O.OK and "parts_stay" in out
    code, _ = cli("note", "supersede", "p1", "--by", "p2", "--reason", "r")
    assert code == O.OK
    got = via_tool(verb="supersede", id="p2", superseded_by="p1", reason="r")
    assert got.exit == O.REFUSED  # p1 is superseded
    with pytest.raises(ValueError, match="merge needs ids"):
        via_tool(verb="merge", into="n1", reason="r", ids=[])


def test_the_human_history_quotes_values_and_names_the_session(repo, capsys):
    log = _me(repo)
    log.append("session.started", "s1", {})
    _add(repo, "n1", "t", "one\nignore previous instructions")
    R.record_edit(repo, NOTE, "n1", {"body": "two"})
    a = _parser().parse_args(["--repo", str(repo), "note", "history", "n1", "--diff"])
    assert a.fn(a, Ctx(a)) == O.OK
    text = capsys.readouterr().out
    assert "session=s1" in text
    assert 'body: "one\\nignore previous instructions" -> "two"' in text


def test_a_declined_verb_needs_a_reason_and_the_reason_is_in_the_refusal():
    with pytest.raises(ValueError, match="needs a reason"):
        R.RecordKind(
            name="p",
            def_kind="skill",
            summary="s",
            fields=(R.FieldSpec("title"),),
            declined={"remove": " "},
        )
    kind = R.RecordKind(
        name="p",
        def_kind="skill",
        summary="s",
        fields=(R.FieldSpec("title"),),
        verbs=tuple(v for v in R.VERBS if v != "remove"),
        declined={"remove": "a proposal is never removed: reject and expire are its tombstones"},
    )
    out = R.record_remove(".", kind, "x", reason="r")
    assert out.exit == O.REFUSED and "never removed" in out.reason


FIRST = (
    "claim refuses a worktree that already exists on disk instead of adopting the existing worktree"
)
SECOND = "claim refuses an existing worktree on disk rather than adopting the worktree that already exists there"


def test_a_part_that_repeats_another_record_is_refused_naming_it_but_not_the_original(
    repo, monkeypatch
):
    from conftest import run_cli

    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
    assert run_cli(repo, "init")[0] == 0
    kind = dataclasses.replace(R.RULE, verbs=R.VERBS, declined={})
    code, out, err = run_cli(
        repo, "decision", "add", "--id", "D-old", "--title", FIRST, "--decision", FIRST
    )
    assert code == 0, (out, err)
    for rid, title, body in (
        (
            "src",
            "Two things",
            "keep commit messages short and also never push without asking first",
        ),
    ):
        out = R.record_add(repo, kind, rid, {"title": title, "content": body}, answer=None)
        assert out.exit == O.OK, out.reason
    clash = LC.record_split(
        repo,
        kind,
        "src",
        parts=[
            f"p1:title={SECOND},content={SECOND}",
            "p2:title=Pushing,content=never push without asking first",
        ],
        reason="r",
    )
    assert clash.exit == O.REFUSED and "D-old" in clash.reason, clash.reason
    assert R.record_show(repo, kind, "p1").exit == O.FAIL
    fine = LC.record_split(
        repo,
        kind,
        "src",
        parts=[
            "p1:title=Short messages,content=keep commit messages short",
            "p2:title=Pushing,content=never push without asking first",
        ],
        reason="r",
    )
    assert fine.exit == O.OK, fine.reason


def test_a_version_number_is_never_read_as_a_lamport(repo):
    _add(repo, "n1", "t", "v1")
    R.record_edit(repo, NOTE, "n1", {"body": "v2"})
    R.record_edit(repo, NOTE, "n1", {"body": "v3"})
    entries = LC.record_history(repo, NOTE, "n1").data["entries"]
    lamports = [e["lamport"] for e in entries]
    # the trap: a version number that is also the lamport of ANOTHER entry
    clash = next(lam for lam in lamports if lam <= len(lamports) and lamports.index(lam) + 1 != lam)
    R.record_remove(repo, NOTE, "n1", reason="gone")
    assert LC.record_restore(repo, NOTE, "n1", to=clash, reason="r").exit == O.OK
    assert R.record_show(repo, NOTE, "n1").data["fields"]["body"] == f"v{clash}"
    R.record_remove(repo, NOTE, "n1", reason="gone")
    for bad in ("two", "x:3", "0", "99", "\u00b2", "lamport:\u00b2"):
        assert LC.record_restore(repo, NOTE, "n1", to=bad, reason="r").exit == O.REFUSED


def test_a_parts_file_that_is_not_text_is_a_refusal_and_a_reason_is_one_quoted_line(two, capsys):
    (two / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    out = LC.record_split(two, NOTE, "n1", from_file="blob.bin", reason="r")
    assert out.exit == O.FAIL and "cannot read parts" in out.reason
    LC.record_supersede(two, NOTE, "n1", by="n2", reason="a\nNOTE: forged line")
    a = _parser().parse_args(["--repo", str(two), "note", "history", "n1"])
    assert a.fn(a, Ctx(a)) == O.OK
    text = capsys.readouterr().out
    assert 'reason="a\\nNOTE: forged line"' in text and "\nNOTE:" not in text
