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
    BEGIN,
    CURRENT,
    END,
    FORM_AIDER,
    FORM_BLOCK,
    FORM_WHOLE,
    MISSING,
    NATIVE_RULES,
    NO_BLOCK,
    NOT_BINDING,
    STALE,
    project_section,
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
    canonical = project_section().replace(BEGIN, "").replace(END, "").strip()
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
        if BEGIN in text and END in text:
            body = text[text.index(BEGIN) + len(BEGIN) : text.index(END)]
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
        assert (repo / rel).read_text().count(BEGIN) <= 1, f"{rel} has two managed blocks"


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
    assert BEGIN in text, f"{rule.path}: no managed block was added"


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
