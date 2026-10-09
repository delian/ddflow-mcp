"""One guidance engine for rules and decisions (B-uni-guidance-core, D-unify).

Pins what the engine shares: the record, the resolver, the authored-file format, the limits
and the similarity score; that a rule file is read and written byte for byte as before; that
the decision lookups answer what the two inline filters did; and the ratchet that no rule- or
decision-specific glob matcher or tokenizer is left behind.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ddflow.core.model import State
from ddflow.core.records import Decision
from ddflow.core.schedule import conflicts
from ddflow.services.guidance import fileformat, similarity
from ddflow.services.guidance.kinds import (
    DECISION,
    KINDS,
    RULE,
    decision_record,
    governing,
    is_valid_rule_id,
)
from ddflow.services.guidance.limits import lint, over_limit
from ddflow.services.guidance.record import GuidanceRecord, Scope
from ddflow.services.guidance.resolve import resolve
from ddflow.services.guidance.store import GuidanceFiles
from ddflow.services.rules import Rule, RulesStorage

ROOT = Path(__file__).resolve().parents[1]


def _rec(rid: str = "g-1", **kw) -> GuidanceRecord:
    return GuidanceRecord(id=rid, kind="rule", title=kw.pop("title", rid), **kw)


# -- the authored file --------------------------------------------------------------------

LEGACY = (
    'id = "r-naming"\n'
    'title = "Naming conventions"\n'
    'tags = ["naming", "style"]\n'
    'scope = "project"\n'
    "priority = 75\n"
    'globs = ["**/*.py", "**/*.js"]\n'
    'created = "2026-10-03T12:00:00"\n'
    'updated = "2026-10-03T12:00:00"\n'
    "\n"
    "Follow snake_case for Python functions."
)


def test_a_rule_file_written_before_the_engine_is_written_back_unchanged() -> None:
    rec = fileformat.parse(LEGACY, RULE)
    assert fileformat.render(rec, RULE) == LEGACY
    assert Rule.from_toml(LEGACY).to_toml() == LEGACY


def test_a_file_with_the_shared_keys_round_trips() -> None:
    rec = _rec(
        "r-x",
        body="text",
        scope=Scope(globs=("src/**",), categories=("testing",), gates=("critic",)),
        level="project",
        category="testing",
        enforcement="block",
        status="proposed",
        owner="ops",
        review_by="2027-01-31",
        sources=["docs/adr/1.md"],
        checks=[{"kind": "pattern", "regex": "TODO"}],
        links=["D-1"],
        provenance={"created": "2026-01-01T00:00:00", "updated": "2026-01-01T00:00:00"},
    )
    text = fileformat.render(rec, RULE)
    assert fileformat.parse(text, RULE) == rec


def test_a_stamped_record_without_provenance_is_stamped_on_write_not_a_crash() -> None:
    text = fileformat.render(GuidanceRecord(id="r-z", kind="rule", title="z", body="b"), RULE)
    got = fileformat.parse(text, RULE)
    assert got.provenance["created"] and got.provenance["updated"]


def test_a_bare_string_is_one_pattern_not_its_characters() -> None:
    text = 'id = "r-x"\ntitle = "x"\nglobs = "*.py"\ntags = "style"\n\nb'
    rec = fileformat.parse(text, RULE)
    assert rec.scope.globs == ("*.py",) and rec.tags == ["style"]
    with pytest.raises(ValueError, match="globs must be a string or a list of strings"):
        fileformat.parse(text.replace('"*.py"', "[1]"), RULE)


def test_enforcement_is_carried_by_the_file_not_dropped() -> None:
    text = 'id = "D-b"\ntitle = "t"\nenforcement = "block"\n\nbody'
    rec = fileformat.parse(text, DECISION)
    assert rec.enforcement == "block" and 'enforcement = "block"' in fileformat.render(
        rec, DECISION
    )
    assert (
        fileformat.parse(text.replace('enforcement = "block"\n', ""), DECISION).enforcement
        == "warn"
    )


def test_checks_are_tables_never_exploded_characters() -> None:
    text = 'id = "r-x"\ntitle = "x"\nchecks = "pytest -q"\n\nb'
    with pytest.raises(ValueError, match="checks must be a list of tables"):
        fileformat.parse(text, RULE)


def test_a_decision_file_uses_the_same_schema() -> None:
    rec = GuidanceRecord(
        id="D-sqlite",
        kind="decision",
        title="Use sqlite",
        body="the index lives in sqlite",
        scope=Scope(globs=("ddflow/infra/*",)),
        enforcement="warn",
        ext={
            "context": "a\n\nb",
            "consequences": "c",
            "alternatives": "",
            "decided_by": "operator",
            "item": "",
            "supersedes": ["D-old"],
            "superseded_by": "",
        },
    )
    text = fileformat.render(rec, DECISION)
    assert "\n\n\n" not in text.split("\n\n", 1)[0], "the frontmatter has no blank line"
    assert fileformat.parse(text, DECISION) == rec


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('title = "x"\n\nbody', "Rule must have an 'id' field"),
        ('id = "r-x"\n\nbody', "Rule must have a 'title' field"),
        ('id = "r-x"\ntitle = "x"', "Rule must have a 'content' field or content block"),
        ('id = "naming"\ntitle = "x"\n\nb', "Invalid rule id 'naming': must be kebab-case"),
        ('id = "r-x"\ntitle = "x"\nstatus = "wip"\n\nb', "unknown status 'wip'"),
        ('id = "r-x"\ntitle = "x"\nenforcement = "x"\n\nb', "unknown enforcement 'x'"),
        ('id = "r-x"\ntitle = "x"\nreview_by = "soon"\n\nb', "review_by 'soon' is not a date"),
        ("id = = x\n\nb", "Invalid TOML in rule frontmatter"),
    ],
)
def test_a_file_that_is_not_guidance_is_refused_with_the_reason(text, message) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        fileformat.parse(text, RULE)


def test_a_decision_id_only_has_to_exist() -> None:
    rec = GuidanceRecord(id="", kind="decision", title="t")
    assert lint(rec, DECISION) == ["Invalid decision id '': must not be empty"]
    assert is_valid_rule_id("r-a-b1") and not is_valid_rule_id("r-A") and not is_valid_rule_id("a")


def test_listing_rules_skips_a_file_that_is_not_one_and_creates_the_directory(tmp_path) -> None:
    store = RulesStorage(tmp_path)
    assert store.list() == [] and store.rules_dir.is_dir()
    (store.rules_dir / "r-bad.toml").write_text('title = "x"\n\nb')
    (store.rules_dir / "r-bad-id.toml").write_text('id = "not-a-rule-id"\ntitle = "x"\n\nb')
    store.add(Rule(id="r-ok", title="t", content="c"))
    assert [r.id for r in store.list()] == ["r-ok"]


def test_a_shipped_record_counts_as_existing_for_a_new_one(tmp_path: Path) -> None:
    files = GuidanceFiles(tmp_path, RULE)
    shipped = files.loader.shipped_path("r-acl")
    shipped.parent.mkdir(parents=True, exist_ok=True)
    try:
        shipped.write_text(
            fileformat.render(_rec("r-acl", body="t", level="project", provenance=_stamps()), RULE)
        )
        assert files.read("r-acl").title == "r-acl"
        with pytest.raises(ValueError, match="already exists"):
            files.write(_rec("r-acl", provenance=_stamps()), new=True)
    finally:
        shipped.unlink()
        if not any(shipped.parent.iterdir()):
            shipped.parent.rmdir()


def test_editing_a_rule_keeps_the_keys_a_person_put_in_the_file(tmp_path: Path) -> None:
    store = RulesStorage(tmp_path)
    rich = LEGACY.replace(
        "priority = 75\n",
        'priority = 75\ncategories = ["testing"]\nowner = "ops"\nreview_by = "2027-01-31"\n',
    )
    store.rules_dir.mkdir(parents=True)
    store.rules_dir.joinpath("r-naming.toml").write_text(rich)
    store.update("r-naming", priority=90)
    text = store.rules_dir.joinpath("r-naming.toml").read_text()
    assert 'categories = ["testing"]' in text and 'owner = "ops"' in text
    assert "priority = 90" in text and 'review_by = "2027-01-31"' in text


# -- the files of a kind ------------------------------------------------------------------


def test_files_are_listed_in_file_name_order_and_bad_ones_reported(tmp_path: Path) -> None:
    files = GuidanceFiles(tmp_path, RULE)
    for rid in ("r-foo", "r-foo-bar", "r-a"):
        files.write(_rec(rid, body="t", level="project", provenance=_stamps()))
    (files.directory / "r-bad.toml").write_text('id = "r-bad"\n')
    # "r-foo-bar.toml" sorts before "r-foo.toml" ('-' < '.'), not after it as the bare ids do
    assert [r.id for r in files.all()] == ["r-a", "r-foo-bar", "r-foo"]
    (problem,) = files.problems()
    assert problem.path.name == "r-bad.toml" and "must have a 'title' field" in problem.message
    with pytest.raises(FileNotFoundError, match="Rule r-none not found at"):
        files.read("r-none")
    with pytest.raises(ValueError, match="already exists"):
        files.write(_rec("r-a", provenance=_stamps()), new=True)
    files.delete("r-a")
    with pytest.raises(ValueError, match="not found at"):
        files.delete("r-a")


def test_an_unreadable_file_is_reported_not_raised(tmp_path: Path) -> None:
    files = GuidanceFiles(tmp_path, RULE)
    files.ensure_dir()
    (files.directory / "r-x.toml").mkdir()  # a directory where the file should be: names() skips it
    (files.directory / "r-y.toml").write_bytes(b"\xff\xfe id = ")
    (problem,) = files.problems()
    assert problem.path.name == "r-y.toml"


def test_a_toml_syntax_error_is_reported_with_its_line(tmp_path: Path) -> None:
    files = GuidanceFiles(tmp_path, RULE)
    files.ensure_dir()
    (files.directory / "r-x.toml").write_text('id = "r-x"\ntitle = \n')
    (problem,) = files.problems()
    assert problem.line == 2


def _stamps() -> dict:
    return {"created": "2026-01-01T00:00:00", "updated": "2026-01-01T00:00:00"}


# -- applicability ------------------------------------------------------------------------


def test_guidance_with_no_restriction_applies_always() -> None:
    (hit,) = resolve([_rec()], globs=["a.py"])
    assert hit.reason == "always"
    assert [h.reason for h in resolve([_rec()])] == ["always"]


def test_guidance_that_sets_no_dimension_reports_always() -> None:
    assert Scope().always and [h.reason for h in resolve([_rec(scope=Scope())])] == ["always"]


def test_globs_gates_and_categories_each_restrict_and_together_all_must_hold() -> None:
    by_globs = _rec("g", scope=Scope(globs=("src/**",)))
    by_gate = _rec("h", scope=Scope(gates=("critic",)))
    by_cat = _rec("c", scope=Scope(categories=("testing",)))
    both = _rec("b", scope=Scope(globs=("src/**",), gates=("critic",)))
    recs = [by_globs, by_gate, by_cat, both]

    def ids(**kw):
        return [h.record.id for h in resolve(recs, **kw)]

    assert ids(globs=["src/a.py"]) == ["g"]
    assert ids(gate="critic") == ["h"]
    assert ids(categories=["testing"]) == ["c"]
    assert ids(globs=["src/a.py"], gate="critic") == ["g", "h", "b"]
    assert ids(globs=["docs/x.md"], gate="critic") == ["h"]
    assert ids() == []


def test_only_live_guidance_is_handed_over_highest_priority_first() -> None:
    recs = [
        _rec("low", priority=10),
        _rec("old", status="deprecated"),
        _rec("top", priority=90),
        _rec("mid", priority=50),
        _rec("tie", priority=50),
        GuidanceRecord(id="gone", kind="decision", status="accepted", ext={"superseded_by": "x"}),
    ]
    assert [h.record.id for h in resolve(recs)] == ["top", "mid", "tie", "low"]


def test_shared_globs_overlap_nothing() -> None:
    rec = _rec("g", scope=Scope(globs=("CHANGELOG.md",)))
    assert resolve([rec], globs=["CHANGELOG.md"])
    assert not resolve([rec], globs=["CHANGELOG.md"], shared=["CHANGELOG.md"])


# -- the decision lookups ------------------------------------------------------------------


def _decisions() -> State:
    st = State()
    for i, (globs, status, by) in enumerate(
        [
            (["ddflow/infra/*"], "accepted", ""),
            ([], "accepted", ""),
            (["docs/**"], "accepted", ""),
            (["ddflow/**"], "accepted", "D-next"),
            ([], "proposed", ""),
            ([], "accepted", "D-next"),
            (["other/x.py"], "accepted", ""),
            ([], "accepted", ""),
        ]
    ):
        st.decisions[f"D-{i}"] = Decision(
            id=f"D-{i}", title=f"t{i}", globs=globs, status=status, superseded_by=by
        )
    return st


@pytest.mark.parametrize(
    "globs",
    [["ddflow/infra/log.py"], ["docs/a.md", "other/x.py"], ["README.md"], [], ["ddflow/**"]],
)
def test_governing_answers_what_the_two_inline_filters_did(globs) -> None:
    st = _decisions()
    hits = [d for d in st.decisions.values() if d.live and d.globs and conflicts(globs, d.globs)]
    wide = [d for d in st.decisions.values() if d.live and not d.globs]
    assert governing(st, globs) == (hits, wide)


def test_a_decision_as_guidance() -> None:
    d = Decision(id="D-1", title="T", decision="do x", globs=["a/*"], context="why", tags=["t"])
    rec = decision_record(d)
    assert (rec.kind, rec.title, rec.body, rec.scope.globs) == ("decision", "T", "do x", ("a/*",))
    assert rec.ext["context"] == "why" and rec.enforcement == "warn" and rec.live
    assert KINDS == {"rule": RULE, "decision": DECISION}


# -- limits -------------------------------------------------------------------------------


def _cfg(**over):
    rules = {
        "max_rules": 2,
        "max_size_bytes": 10,
        "scopes_allowed": ["project"],
        "tags_allowed": [],
    } | over
    return SimpleNamespace(rules=SimpleNamespace(**rules))


@pytest.mark.parametrize(
    ("rec", "cfg", "existing", "message"),
    [
        (
            _rec(body="x" * 11, level="project"),
            _cfg(),
            0,
            "content is over rules.max_size_bytes (10)",
        ),
        (_rec(level="task"), _cfg(), 0, "scope 'task' is not in rules.scopes_allowed ['project']"),
        (
            _rec(level="project", tags=["a", "b"]),
            _cfg(tags_allowed=["a"]),
            0,
            "tags ['b'] are not in rules.tags_allowed ['a']",
        ),
        (_rec(level="project"), _cfg(), 2, "the project already has rules.max_rules (2) rules"),
        (_rec(level="project", tags=["z"]), _cfg(), 1, ""),
    ],
)
def test_limits_say_what_they_were_broken_by(rec, cfg, existing, message) -> None:
    assert over_limit(rec, RULE.limits(cfg), lambda: existing) == message


def test_the_count_is_read_only_when_the_other_limits_hold() -> None:
    def boom() -> int:
        raise AssertionError("counted the records for a refusal that does not need it")

    lim = RULE.limits(_cfg())
    assert "max_size_bytes" in over_limit(_rec(body="x" * 11, level="project"), lim, boom)
    assert "scopes_allowed" in over_limit(_rec(level="task"), lim, boom)


def test_no_allowed_scopes_means_none_is_allowed_as_it_always_did() -> None:
    """An empty ``scopes_allowed`` refuses every scope (the tag list is the one where empty
    means anything goes); the config default lists three, so a project never meets it."""
    msg = over_limit(_rec(level="project"), RULE.limits(_cfg(scopes_allowed=[])), lambda: 0)
    assert msg == "scope 'project' is not in rules.scopes_allowed []"


def test_a_kind_with_no_config_section_has_no_limits() -> None:
    assert DECISION.limits(_cfg()) is None
    assert over_limit(_rec(body="x" * 10**6), None, lambda: 10**6) == ""


# -- similarity ---------------------------------------------------------------------------


def test_jaccard_counts_shared_words_of_three_letters_or_more() -> None:
    assert similarity.jaccard("", "") == 1.0
    assert similarity.jaccard("", "x") == 0.0
    assert similarity.jaccard("a b", "a b") == 1.0, "no word at all: equal texts are the same"
    assert similarity.jaccard("a b", "b a") == 0.0
    assert similarity.jaccard("Use snake_case now", "use SNAKE_CASE") == pytest.approx(2 / 3)
    assert similarity.words("Use 'snake_case'! Test-case.") == ["use", "snake_case", "test-case"]


def test_similar_compares_titles_for_title_only_guidance_and_orders_by_score() -> None:
    a = _rec("a", title="tests come first", body="")
    b = _rec("b", title="docs live in docs", body="")
    c = _rec("c", title="tests come first always", body="")
    got = similarity.similar([a, b, c], "", title="tests come first", threshold=0.5)
    assert [(s.record.id, s.score) for s in got] == [("a", 1.0), ("c", 0.75)]
    assert got[0].overlap == ["come", "first", "tests"]
    assert similarity.similar([a], "x y z w", title="", threshold=0.5) == []
    # best first whatever the order given: the lower score listed first still comes second
    flipped = similarity.similar([c, b, a], "", title="tests come first", threshold=0.5)
    assert [s.record.id for s in flipped] == ["a", "c"]


# -- the ratchet --------------------------------------------------------------------------

#: What is left of the old rule- and decision-specific stacks, by module: nothing of these.
_FORBIDDEN = {
    "ddflow/services/rules.py": (
        "fnmatch",
        "textsim",
        "_globs_match",
        "_tokenize",
        "matches_globs",
    ),
    "ddflow/api/rules.py": ("fnmatch", "textsim", "_get_overlap_terms", "_tokenize", "_compared"),
    "ddflow/api/decisions.py": ("conflicts",),
    "ddflow/api/lifecycle/brief.py": ("conflicts",),
}


@pytest.mark.parametrize("path", sorted(_FORBIDDEN))
def test_no_rule_or_decision_specific_matcher_or_tokenizer_remains(path) -> None:
    source = (ROOT / path).read_text("utf-8")
    left = [w for w in _FORBIDDEN[path] if re.search(rf"\b{re.escape(w)}\b", source)]
    assert not left, (path, left)


# -- bug Bc956cae0e0: a refusal by a [rules] limit said "added rule" and not why -----------


def test_a_rule_refused_by_a_limit_says_why_and_does_not_say_added(repo) -> None:
    from conftest import run_cli

    code, out, err = run_cli(
        repo, "rule", "add", "--id", "r-x", "--title", "t", "--content", "c", "--scope", "nope"
    )
    assert code == 3
    assert "added" not in out and "scope 'nope' is not in rules.scopes_allowed" in err
    assert not (repo / ".ddflow" / "rules" / "r-x.toml").exists()


def test_brief_hands_over_the_decisions_decision_applicable_names(repo) -> None:
    """`brief` and `decision applicable` ask the one resolver: the same decisions. The brief
    leads with the project-wide (pinned) ones -- the injection path's order, so a section trim
    never reaches them (B-uni-guidance-inject) -- then those the item's files select."""
    import json

    from conftest import run_cli

    for did, globs in (("D-a", "ddflow/infra/*"), ("D-b", ""), ("D-c", "docs/**")):
        args = ["decision", "add", "--id", did, "--title", did, "--decision", f"do {did}"]
        assert run_cli(repo, *args, *(["--globs", globs] if globs else []))[0] == 0
    assert (
        run_cli(repo, "task", "add", "T1", "--title", "infra", "--globs", "ddflow/infra/log.py")[0]
        == 0
    )
    out = run_cli(repo, "--json", "decision", "applicable", "T1")[1]
    body = json.loads(out)
    named = [d["id"] for d in body["applicable"]] + [d["id"] for d in body["project_wide"]]
    assert named == ["D-a", "D-b"]
    brief = run_cli(repo, "brief", "--item", "T1")[1]
    mentioned = sorted(
        (brief.index(f'id="{d}"'), d) for d in ("D-a", "D-b", "D-c") if f'id="{d}"' in brief
    )
    assert [d for _, d in mentioned] == [d["id"] for d in body["project_wide"]] + [
        d["id"] for d in body["applicable"]
    ]
    assert sorted(named) == sorted(d for _, d in mentioned)
