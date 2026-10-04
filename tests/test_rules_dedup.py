"""Rule deduplication checking on add/edit.

Decision D-no-duplicates applies to rules as well. When adding a rule, check similarity
against existing rules before filing. Above threshold (0.55): ask for decision. Below
threshold (0.35): file silently. In between: warn and ask.

Tests cover:
- Identical content → auto-merged into existing
- Similar content (score 0.55+) → ask for decision
- Below threshold → file new with candidates listed
- --check flag → dry run, no write
- Answers: --new, --extends ID, --duplicate-of ID
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow import api as A
from ddflow.api.rules import RuleDedupAnswer, rule_dedup_check, rule_dedup_check_dry_run
from ddflow.services.rules import Rule, RulesStorage


@pytest.fixture
def repo(tmp_path):
    """A test repository with git initialized."""
    import subprocess

    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo


@pytest.fixture
def rules_storage(repo):
    """A RulesStorage instance."""
    return RulesStorage(repo)


def test_rule_dedup_check_identical_content():
    """Identical content scores 1.0."""
    content = "Follow snake_case for function names"
    rule1 = Rule(id="r-naming", title="Naming conventions", content=content)
    rule2 = Rule(id="r-style", title="Code style", content="Different content")

    is_dup, candidates = rule_dedup_check(content, [rule1, rule2])

    assert is_dup is True
    assert len(candidates) == 1
    assert candidates[0]["id"] == "r-naming"
    assert candidates[0]["score"] == 1.0


def test_rule_dedup_check_similar_content():
    """Similar content above threshold is returned."""
    existing_content = "Use snake_case for function names and variables"
    new_content = "Use snake_case for constants"  # Shares significant tokens (score ~0.375)

    rule = Rule(id="r-naming", title="Naming conventions", content=existing_content)

    is_dup, candidates = rule_dedup_check(new_content, [rule], threshold=0.35)

    assert is_dup is True
    assert len(candidates) == 1
    assert candidates[0]["id"] == "r-naming"
    # Score should be between 0.35 and 1.0
    assert 0.35 <= candidates[0]["score"] < 1.0


def test_rule_dedup_check_below_threshold():
    """Content below threshold is not flagged as duplicate."""
    existing_content = "Use snake_case for function names"
    new_content = "Write unit tests for all functions"

    rule = Rule(id="r-naming", title="Naming conventions", content=existing_content)

    is_dup, candidates = rule_dedup_check(new_content, [rule], threshold=0.55)

    assert is_dup is False
    assert len(candidates) == 0


def test_rule_dedup_check_empty_content():
    """Empty content scores 1.0 with other empty content."""
    rule = Rule(id="r-empty", title="Empty rule", content="")

    is_dup, candidates = rule_dedup_check("", [rule])

    assert is_dup is True
    assert candidates[0]["score"] == 1.0


def test_rule_dedup_check_multiple_candidates():
    """Multiple similar rules are returned sorted by score."""
    new_content = "Use snake_case for function names"
    rule1 = Rule(
        id="r-naming",
        title="Naming conventions",
        content="Use snake_case for function names",  # Identical - score 1.0
    )
    rule2 = Rule(
        id="r-style",
        title="Code style",
        content="Use snake_case for constants",  # Similar - score ~0.5
    )
    rule3 = Rule(
        id="r-testing",
        title="Testing",
        content="Write unit tests",  # Different - score low
    )

    is_dup, candidates = rule_dedup_check(new_content, [rule1, rule2, rule3], threshold=0.4)

    assert is_dup is True
    # Should have rule1 as candidate (score 1.0)
    assert len(candidates) >= 1
    assert candidates[0]["id"] == "r-naming"  # Identical match should be first
    # Candidates sorted by score descending
    if len(candidates) > 1:
        assert candidates[0]["score"] >= candidates[1]["score"]


def test_rule_dedup_check_includes_overlap_terms():
    """Candidates include overlap terms for transparency."""
    existing_content = "Use snake_case for function names and constants"
    new_content = "Use snake_case for constants and methods"  # Shares "snake", "case", "constants"

    rule = Rule(id="r-naming", title="Naming conventions", content=existing_content)

    is_dup, candidates = rule_dedup_check(new_content, [rule], threshold=0.3)

    assert is_dup is True
    assert "overlap" in candidates[0]
    # Should have some overlap terms
    assert len(candidates[0]["overlap"]) > 0
    # Overlap should contain shared significant terms
    assert any(
        term in ["snake", "case", "constants", "function"] for term in candidates[0]["overlap"]
    )


def test_rule_add_without_dedup():
    """Adding a rule with check_dedup=False skips duplicate checking."""
    rule = Rule(id="r-test", title="Test Rule", content="This is a test rule")

    # Add with dedup disabled
    outcome = A.rule_add(Path("/tmp/nonexistent"), rule, check_dedup=False)

    # Should attempt to write (and fail because path doesn't exist)
    # but not due to duplicate check
    assert "duplicate" not in outcome.reason.lower()


def test_rule_add_identical_content_refused():
    """Adding a rule identical to existing one is refused."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        # Add first rule
        rule1 = Rule(
            id="r-naming", title="Naming conventions", content="Use snake_case for function names"
        )
        outcome1 = A.rule_add(repo, rule1)
        assert outcome1.exit == 0, outcome1.reason

        # Try to add identical rule
        rule2 = Rule(
            id="r-naming-2",
            title="Naming conventions 2",
            content="Use snake_case for function names",
        )
        outcome2 = A.rule_add(repo, rule2)

        # Should be refused with candidates
        assert outcome2.exit == 3, f"Expected refusal, got {outcome2.exit}: {outcome2.reason}"
        assert "duplicate" in outcome2.reason.lower()
        assert "r-naming" in outcome2.data.get("candidates", [{}])[0].get("id", "")


def test_rule_add_answer_new():
    """Adding a rule with answer='new' bypasses duplicate check."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        # Add first rule
        rule1 = Rule(
            id="r-naming", title="Naming conventions", content="Use snake_case for function names"
        )
        outcome1 = A.rule_add(repo, rule1)
        assert outcome1.exit == 0

        # Add similar rule with answer="new"
        rule2 = Rule(
            id="r-naming-2",
            title="Naming conventions 2",
            content="Use snake_case for function names",
        )
        answer = RuleDedupAnswer("new", "")
        outcome2 = A.rule_add(repo, rule2, dedup_answer=answer)

        # Should succeed
        assert outcome2.exit == 0, f"Expected success, got: {outcome2.reason}"

        # Both rules should exist
        storage = RulesStorage(repo)
        rules = storage.list()
        assert len(rules) == 2


def test_rule_add_answer_extends():
    """Adding a rule with answer='extends ID' appends to existing rule."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        # Add first rule
        rule1 = Rule(
            id="r-naming",
            title="Naming conventions",
            content="Use snake_case for function names and variables",
        )
        outcome1 = A.rule_add(repo, rule1, check_dedup=False)
        assert outcome1.exit == 0

        # Add similar rule with answer="extends r-naming"
        # Use content that will score above 0.4 threshold
        new_content = "Use snake_case for constants and methods"
        rule2 = Rule(id="r-naming-2", title="Naming conventions 2", content=new_content)
        answer = RuleDedupAnswer("extends", "r-naming")
        # Lower threshold to 0.4 to trigger duplicate detection
        outcome2 = A.rule_add(repo, rule2, dedup_answer=answer, dedup_threshold=0.4)

        # Should succeed with extension
        assert outcome2.exit == 0, f"Expected success, got: {outcome2.reason}"
        assert outcome2.data.get("extended") == "r-naming"

        # Original rule should have extended content
        storage = RulesStorage(repo)
        extended_rule = storage.get("r-naming")
        assert new_content in extended_rule.content
        assert "---" in extended_rule.content  # Should have separator


def test_rule_dedup_check_dry_run_with_candidates():
    """Dry-run shows candidates without writing."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        # Add a rule
        rule1 = Rule(
            id="r-naming",
            title="Naming conventions",
            content="Use snake_case for function names and variables",
        )
        A.rule_add(repo, rule1, check_dedup=False)

        # Dry-run check with similar content (uses lower threshold)
        outcome = rule_dedup_check_dry_run(repo, "Use snake_case for constants", threshold=0.35)

        # Should show candidates
        assert outcome.exit == 0
        assert "candidates" in outcome.data
        assert len(outcome.data["candidates"]) > 0


def test_rule_dedup_check_dry_run_no_candidates():
    """Dry-run with no matches returns nothing."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        # Add a rule
        rule1 = Rule(
            id="r-naming", title="Naming conventions", content="Use snake_case for function names"
        )
        A.rule_add(repo, rule1, check_dedup=False)

        # Dry-run check with very different content
        outcome = rule_dedup_check_dry_run(repo, "Write unit tests for everything")

        # Should return nothing
        assert outcome.exit == 2  # nothing
        assert "candidates" in outcome.data


def test_rule_dedup_answer_parse():
    """RuleDedupAnswer.parse handles various formats."""
    # Parse "new"
    ans = RuleDedupAnswer.parse("new")
    assert ans.relation == "new"
    assert ans.target == ""

    # Parse "extends ID"
    ans = RuleDedupAnswer.parse("extends r-naming")
    assert ans.relation == "extends"
    assert ans.target == "r-naming"

    # Parse with colon
    ans = RuleDedupAnswer.parse("extends:r-naming")
    assert ans.relation == "extends"
    assert ans.target == "r-naming"

    # Parse "duplicate" alias
    ans = RuleDedupAnswer.parse("duplicate r-naming")
    assert ans.relation == "duplicate_of"
    assert ans.target == "r-naming"


def test_rule_dedup_answer_validation():
    """RuleDedupAnswer validates correctly."""
    # Valid answer
    ans = RuleDedupAnswer("new", "")
    assert ans.problem == ""

    # Invalid relation
    ans = RuleDedupAnswer("invalid", "")
    assert "unknown answer" in ans.problem

    # new with target
    ans = RuleDedupAnswer("new", "r-naming")
    assert "new" in ans.problem

    # extends without target
    ans = RuleDedupAnswer("extends", "")
    assert "extends" in ans.problem and "needs" in ans.problem


def test_rule_similarity_score_consistency():
    """Rule.similarity_score is consistent across calls."""
    content1 = "Use snake_case for function names"
    content2 = "Functions should use snake_case"

    rule = Rule(id="r-naming", title="Naming", content=content1)

    score1 = rule.similarity_score(content2)
    score2 = rule.similarity_score(content2)

    assert score1 == score2


def test_rule_add_answer_invalid_target():
    """Adding with invalid target rule ID fails."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        # Add a rule first
        rule1 = Rule(
            id="r-naming",
            title="Naming conventions",
            content="Use snake_case for function names and variables",
        )
        A.rule_add(repo, rule1, check_dedup=False)

        # Try to add similar rule with invalid target
        rule2 = Rule(id="r-new", title="New Rule", content="Use snake_case for constants")
        answer = RuleDedupAnswer("extends", "r-nonexistent")
        # Use low threshold to trigger duplicate detection
        outcome = A.rule_add(repo, rule2, dedup_answer=answer, dedup_threshold=0.35)

        # Should fail with error about target not found
        assert outcome.exit != 0
        assert "not found" in outcome.reason.lower()


def test_rule_add_check_flag_behavior():
    """--check flag behaves as expected (dry-run)."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        # Add initial rule
        rule1 = Rule(id="r-naming", title="Naming conventions", content="Use snake_case")
        A.rule_add(repo, rule1, check_dedup=False)

        # Dry-run with identical content
        outcome = rule_dedup_check_dry_run(repo, "Use snake_case")

        # Should show it would be asked
        assert outcome.exit == 0
        assert outcome.data.get("would_ask") is True

        # Verify original rules unchanged
        storage = RulesStorage(repo)
        rules = storage.list()
        assert len(rules) == 1
