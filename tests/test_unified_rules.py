"""B170: one canonical rules text, inlined into every agent's own surface, drift-checked.

The goal is a project that several coding agents can work in at once. `AGENTS.md` is the
cross-agent convention and most of the 22 supported agents read it — but seven do not read
it first, or at all:

    QWEN.md · .clinerules/ · .tabnine/guidelines/ · replit.md · .goosehints ·
    .cursor/rules/*.mdc · Aider's `read:` list

**A pointer stub was the obvious design and this repo had already refuted it**: "a link is
only followed if the agent chooses to follow it"
(`templates/drivers/deltas/kilo-cline.md`). A rule that binds only when the model feels
like opening a file is not an enforced rule. So the block is INLINED into each surface, and
the duplication is owned by a check: one generator, N managed copies, `rules_status()`
comparing every one of them.
"""

from __future__ import annotations

import re

import pytest
from conftest import run_cli

from ddflow.services.adopt import (
    AIDER_READS,
    CURRENT,
    FORM_AIDER,
    FORM_BLOCK,
    FORM_WHOLE,
    MISSING,
    NATIVE_RULES,
    NO_BLOCK,
    NOT_BINDING,
    STALE,
    block_body,
    project_body,
    rules_status,
)


def _adopt(repo) -> None:
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "adopt")[0] == 0


def _states(repo) -> dict[str, str]:
    return {s.path: s.state for s in rules_status(repo)}


def _parse_read_list(text: str) -> list[str]:
    """The `read:` values a YAML parser would see, without taking a yaml dependency.

    Deliberately resolves a duplicate key the way YAML does — last one wins — so the test
    sees what Aider would see rather than what the file happens to contain.
    """
    values: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not re.match(r"^read:", line):
            continue
        values = []  # a later `read:` REPLACES an earlier one
        inline = line.split(":", 1)[1].strip()
        # A comment after the key is not the value. `read: # our docs` followed by a block
        # list is valid YAML whose value is the list; treating the comment as a scalar made
        # this helper report the entries as missing and blame the code for it.
        if inline.startswith("#"):
            inline = ""
        elif "#" in inline and not inline.startswith("["):
            inline = inline.split("#", 1)[0].strip()
        if inline.startswith("["):
            values = [v.strip().strip("'\"") for v in inline.strip("[]").split(",") if v.strip()]
            continue
        if inline:
            values = [inline]
            continue
        for nxt in lines[i + 1 :]:
            m = re.match(r"^\s+-\s*(.+?)\s*$", nxt)
            if not m:
                break
            values.append(m.group(1))
    return values


def test_every_native_surface_carries_the_canonical_text(repo):
    """Not a pointer TO the rules — the rules."""
    _adopt(repo)
    canonical = project_body().strip()
    # A sentence from the block that no pointer stub would contain.
    probe = next(ln for ln in canonical.splitlines() if "claim" in ln.lower())
    for key, rule in NATIVE_RULES.items():
        path = repo / rule.path
        assert path.is_file(), f"{key}: {rule.path} was not written"
        text = path.read_text()
        if rule.form == FORM_AIDER:
            assert AIDER_READS in text, f"{key}: Aider is not told to load {AIDER_READS}"
            continue
        assert probe.strip()[:40] in text, (
            f"{key}: {rule.path} does not contain the rules themselves — if this became a "
            f"pointer, see this module's docstring for why that does not bind"
        )


def test_all_copies_say_the_same_thing(repo):
    """N copies of one text is only safe while they are identical."""
    _adopt(repo)
    bodies = {}
    for key, rule in NATIVE_RULES.items():
        if rule.form == FORM_AIDER:
            continue
        text = (repo / rule.path).read_text()
        if block_body(text) is not None:
            body = block_body(text)
        else:  # FORM_WHOLE: frontmatter, then the body
            body = text.split("---\n\n", 1)[-1]
        bodies[key] = body.strip()
    distinct = set(bodies.values())
    assert len(distinct) == 1, (
        f"the surfaces disagree: {list(bodies)} produced {len(distinct)} different texts"
    )


def test_adopt_is_idempotent_across_every_surface(repo):
    """Re-running must not append a second copy into a file it already wrote."""
    _adopt(repo)
    before = {r.path: (repo / r.path).read_text() for r in NATIVE_RULES.values()}
    assert run_cli(repo, "adopt")[0] == 0
    for rel, text in before.items():
        assert (repo / rel).read_text() == text, f"{rel} changed on a second adopt"
    for rel in before:
        assert (repo / rel).read_text().count("ddflow:begin rules/work-queue") <= 1, (
            f"{rel} has two managed blocks"
        )


@pytest.mark.parametrize("key", sorted(k for k, r in NATIVE_RULES.items() if r.form == FORM_BLOCK))
def test_a_block_surface_preserves_the_operators_own_content(repo, key):
    """`QWEN.md`, `replit.md` and `.goosehints` may already be the project's. A tool that
    overwrites them is a tool nobody runs twice."""
    rule = NATIVE_RULES[key]
    assert run_cli(repo, "init")[0] == 0
    path = repo / rule.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# my own guidance\nkeep me\n")
    assert run_cli(repo, "adopt", "--agents", key)[0] == 0
    text = path.read_text()
    assert "keep me" in text, f"{rule.path}: adopt destroyed the operator's content"
    assert block_body(text) is not None, f"{rule.path}: no managed block was added"


def test_drift_is_reported_for_every_kind_of_break(repo):
    """The point of generating N copies is that a check owns them. Each break below left
    an agent reading absent, incomplete or non-binding rules while `AGENTS.md` sat there
    current and every other surface reported the project as adopted."""
    _adopt(repo)
    assert all(v == CURRENT for v in _states(repo).values()), _states(repo)

    # 1. an edited block
    q = repo / "QWEN.md"
    q.write_text(q.read_text().replace("claim", "grab"))
    assert _states(repo)["QWEN.md"] == STALE

    # 2. markers stripped
    c = repo / ".clinerules" / "ddflow.md"
    c.write_text("# mine now\n")
    assert _states(repo)[".clinerules/ddflow.md"] == NO_BLOCK

    # 3. Aider told to load nothing — silent TOTAL failure, not degraded behaviour
    (repo / ".aider.conf.yml").write_text("model: gpt-4\n")
    assert _states(repo)[".aider.conf.yml"] == NOT_BINDING

    # 4. a Cursor rule that will never be loaded
    m = repo / ".cursor" / "rules" / "ddflow.mdc"
    m.write_text(m.read_text().replace("alwaysApply: true", "alwaysApply: false"))
    assert _states(repo)[".cursor/rules/ddflow.mdc"] == NOT_BINDING

    # 5. deleted outright
    (repo / ".goosehints").unlink()
    assert _states(repo)[".goosehints"] == MISSING


def test_doctor_reports_native_rule_drift_and_fails(repo):
    """A check nothing surfaces is a check nobody runs."""
    _adopt(repo)
    rc, out, _ = run_cli(repo, "doctor")
    assert rc == 0, out

    (repo / ".goosehints").unlink()
    q = repo / "QWEN.md"
    q.write_text(q.read_text().replace("claim", "grab"))
    rc, out, _ = run_cli(repo, "doctor")
    assert rc != 0, "doctor passed a project whose agents have drifted rules"
    assert ".goosehints" in out and "QWEN.md" in out, out


def test_the_not_binding_reason_is_that_surfaces_own(repo):
    """`not_binding` used to render Cursor's reason for every surface, so an operator with a
    broken `.aider.conf.yml` was told `alwaysApply` was not true — about a file with no such
    key. Naming the wrong cause sends the reader to fix something that is not broken."""
    from ddflow.services.adopt import RulesState

    aider = RulesState(".aider.conf.yml", NOT_BINDING).render()
    assert AIDER_READS in aider and "alwaysApply" not in aider, aider
    cursor = RulesState(".cursor/rules/ddflow.mdc", NOT_BINDING).render()
    assert "alwaysApply" in cursor, cursor


def test_aider_gets_a_read_entry_without_losing_its_own(repo):
    """Aider discovers nothing, so the `read:` entry IS the wiring — and the operator's
    existing entries must survive it."""
    assert run_cli(repo, "init")[0] == 0
    (repo / ".aider.conf.yml").write_text("model: gpt-4\nread: CONVENTIONS.md\n")
    assert run_cli(repo, "adopt", "--agents", "aider")[0] == 0
    text = (repo / ".aider.conf.yml").read_text()
    assert "CONVENTIONS.md" in text, f"adopt dropped the operator's read entry:\n{text}"
    assert AIDER_READS in text, text
    assert "model: gpt-4" in text, text
    # EXACTLY one `read:` key. Appending a second one keeps both strings in the file while
    # YAML silently resolves a duplicate key to the LAST occurrence — so the operator's
    # entry is present in the text and gone from the parsed config. A substring assertion
    # passes either way, which is how mutation U6 survived the first version of this test.
    assert len(re.findall(r"^read:", text, re.M)) == 1, (
        f"a duplicate `read:` key — YAML keeps only the last, so one entry is lost:\n{text}"
    )
    # And the parse agrees, which is the only thing Aider will see.
    loaded = _parse_read_list(text)
    assert set(loaded) == {"CONVENTIONS.md", AIDER_READS}, loaded
    assert _states(repo)[".aider.conf.yml"] == CURRENT


def test_every_form_is_exercised_by_this_module():
    """A form with no surface using it is dead code; a form no test covers is worse."""
    forms = {r.form for r in NATIVE_RULES.values()}
    assert forms == {FORM_WHOLE, FORM_BLOCK, FORM_AIDER}, forms


@pytest.mark.parametrize(
    ("initial", "keeps"),
    [
        ("read: [CONVENTIONS.md]\n", "CONVENTIONS.md"),
        ("read: [CONVENTIONS.md]  # our docs\n", "our docs"),
        ("read: []\n", ""),
        ("read: CONVENTIONS.md\n", "CONVENTIONS.md"),
        ("read:\n  - CONVENTIONS.md\n", "CONVENTIONS.md"),
        ("read:\n  - CONVENTIONS.md\nmodel: gpt-4\n", "model: gpt-4"),
        ("model: gpt-4\n", "model: gpt-4"),
    ],
)
def test_every_aider_read_form_is_handled_and_stays_idempotent(repo, initial, keeps):
    """roborev, on 3040d4b: `adopt` wrote a file `doctor` immediately called broken.

    The writer could emit an INLINE list (`read: [CONVENTIONS.md, AGENTS.md]`) while both
    the writer's idempotency guard and `rules_status` matched only the block form. So a
    correctly-adopted project failed `doctor`, and every re-adopt appended again — the file
    grew without bound. Two worse variants: a trailing comment landed INSIDE the brackets
    (`read: [CONVENTIONS.md]  # our docs, AGENTS.md]`, unparseable, taking the operator's
    own entry down with it) and `read: []` became `read: [, AGENTS.md]`.

    Every form now normalises to the block form, so the reader and the writer cannot
    disagree about what they are looking at.
    """
    assert run_cli(repo, "init")[0] == 0
    (repo / ".aider.conf.yml").write_text(initial)
    for _ in range(3):  # idempotent: three adopts, one entry
        assert run_cli(repo, "adopt", "--agents", "aider")[0] == 0

    text = (repo / ".aider.conf.yml").read_text()
    assert text.count(AIDER_READS) == 1, f"the entry was appended repeatedly:\n{text}"
    assert _states(repo)[".aider.conf.yml"] == CURRENT, (
        f"adopt wrote a file rules_status calls broken:\n{text}"
    )
    if keeps:
        assert keeps in text, f"the operator's own content was lost:\n{text}"
    # ...and what Aider will actually parse contains both.
    values = _parse_read_list(text)
    assert AIDER_READS in values, text
    if keeps and keeps.endswith(".md"):
        assert keeps in values, f"{keeps} survived as text but not as a parsed value: {values}"


def test_an_inline_read_list_written_by_hand_is_recognised(repo):
    """ddflow normalises what IT writes, but an operator may have written the inline form
    themselves. Reporting that as broken would send them to fix valid config."""
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "adopt", "--agents", "aider")[0] == 0
    (repo / ".aider.conf.yml").write_text("read: [AGENTS.md, CONVENTIONS.md]\n")
    assert _states(repo)[".aider.conf.yml"] == CURRENT


def test_the_agent_target_record_carries_no_unread_field():
    """A field written by the registry and read by nothing is a second, unowned copy of a
    fact — `NATIVE_RULES` already owns which surface an agent reads, and the two had already
    drifted (`.tabnine/guidelines/ddflow.md` versus `.tabnine/guidelines/`) before anything
    consumed either. Found by roborev on 3040d4b."""
    import dataclasses

    from ddflow.services.adopt import AgentTarget

    names = {f.name for f in dataclasses.fields(AgentTarget)}
    assert names == {"delta", "config", "shape"}, (
        f"AgentTarget grew a field: {names}. Every one must have a reader — check with "
        f"`grep -rn '\\.<field>' ddflow/` before adding it."
    )
