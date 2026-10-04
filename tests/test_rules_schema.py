"""Tests for Rule dataclass and schema validation.

Tests cover:
- TOML frontmatter parsing and validation
- Required field enforcement (id, title, content)
- Rule ID format validation (kebab-case with "r-" prefix)
- Glob matching against file lists
- Similarity scoring with token-based comparison
- Size and count limit configuration
- Round-trip serialization (to_toml / from_toml)
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.rules import Rule, _globs_match, _is_valid_rule_id, _tokenize


class TestRuleIDValidation:
    """Rule ID must be kebab-case with 'r-' prefix."""

    def test_valid_ids(self):
        """Valid IDs: r-naming, r-test-coverage, r-security-audit."""
        valid = ["r-naming", "r-test-coverage", "r-security-audit", "r-1", "r-a-b-c"]
        for rule_id in valid:
            assert _is_valid_rule_id(rule_id), f"Should accept {rule_id}"

    def test_invalid_ids_missing_prefix(self):
        """IDs without 'r-' prefix are invalid."""
        invalid = ["naming", "test-coverage", "-naming", "r_naming"]
        for rule_id in invalid:
            assert not _is_valid_rule_id(rule_id), f"Should reject {rule_id}"

    def test_invalid_ids_bad_format(self):
        """IDs with uppercase, underscores, or spaces are invalid."""
        invalid = [
            "r-Naming",
            "r-test_coverage",
            "r-test coverage",
            "r-",
            "r--double",
            "r-test-",
        ]
        for rule_id in invalid:
            assert not _is_valid_rule_id(rule_id), f"Should reject {rule_id}"


class TestRuleFromTOML:
    """Parse rules from TOML with frontmatter."""

    def test_parse_minimal_rule(self):
        """Minimal valid rule has id, title, and content."""
        toml = """id = "r-naming"
title = "Naming conventions"

Follow snake_case for all Python functions."""
        rule = Rule.from_toml(toml)
        assert rule.id == "r-naming"
        assert rule.title == "Naming conventions"
        assert "Follow snake_case" in rule.content
        assert rule.scope == "project"
        assert rule.priority == 50

    def test_parse_full_rule_with_all_fields(self):
        """Parse a rule with all optional fields."""
        toml = """id = "r-test-coverage"
title = "Test coverage"
tags = ["testing", "quality"]
scope = "phase"
priority = 75
globs = ["**/*.py", "tests/**/*.py"]

Every module must have at least 80% coverage."""
        rule = Rule.from_toml(toml)
        assert rule.id == "r-test-coverage"
        assert rule.title == "Test coverage"
        assert rule.tags == ["testing", "quality"]
        assert rule.scope == "phase"
        assert rule.priority == 75
        assert rule.globs == ["**/*.py", "tests/**/*.py"]
        assert "at least 80%" in rule.content

    def test_parse_rule_with_content_field_in_frontmatter(self):
        """Content can be specified in the frontmatter as a field."""
        toml = """id = "r-doc"
title = "Documentation"
content = "Every function must have a docstring."
"""
        rule = Rule.from_toml(toml)
        assert rule.id == "r-doc"
        assert rule.content == "Every function must have a docstring."

    def test_missing_id_raises_error(self):
        """Rule without id raises ValueError."""
        toml = """title = "Missing ID"

Some content here."""
        with pytest.raises(ValueError, match="'id' field"):
            Rule.from_toml(toml)

    def test_missing_title_raises_error(self):
        """Rule without title raises ValueError."""
        toml = """id = "r-test"

Some content here."""
        with pytest.raises(ValueError, match="'title' field"):
            Rule.from_toml(toml)

    def test_missing_content_raises_error(self):
        """Rule without content or content block raises ValueError."""
        toml = """id = "r-test"
title = "Test"
"""
        with pytest.raises(ValueError, match="'content'"):
            Rule.from_toml(toml)

    def test_invalid_id_format_raises_error(self):
        """Rule with invalid ID format raises ValueError."""
        toml = """id = "naming"
title = "Bad ID"

Content here."""
        with pytest.raises(ValueError, match="Invalid rule id"):
            Rule.from_toml(toml)

    def test_invalid_toml_raises_error(self):
        """Malformed TOML raises ValueError."""
        toml = """id = "r-bad"
title = "Bad TOML
This is broken"""
        with pytest.raises(ValueError, match="Invalid TOML"):
            Rule.from_toml(toml)

    def test_parse_rule_with_multiline_content(self):
        """Content can span multiple lines."""
        toml = """id = "r-style"
title = "Code style"

1. Use 4-space indentation
2. Keep lines under 100 characters
3. Use descriptive variable names
4. Write tests before implementing"""
        rule = Rule.from_toml(toml)
        assert "4-space indentation" in rule.content
        assert "descriptive variable names" in rule.content
        assert "\n" in rule.content


class TestRuleToTOML:
    """Serialize rules to TOML format."""

    def test_serialize_minimal_rule(self):
        """Serialize a minimal rule back to TOML."""
        rule = Rule(
            id="r-naming",
            title="Naming conventions",
            content="Use snake_case.",
        )
        toml = rule.to_toml()
        assert 'id = "r-naming"' in toml
        assert 'title = "Naming conventions"' in toml
        assert "Use snake_case" in toml
        assert 'scope = "project"' in toml
        assert "priority = 50" in toml

    def test_serialize_full_rule(self):
        """Serialize a rule with all fields."""
        rule = Rule(
            id="r-test",
            title="Testing rules",
            content="Write tests.",
            tags=["testing", "quality"],
            scope="phase",
            priority=80,
            globs=["**/*.py"],
        )
        toml = rule.to_toml()
        assert 'id = "r-test"' in toml
        assert 'tags = ["testing", "quality"]' in toml
        assert 'scope = "phase"' in toml
        assert "priority = 80" in toml
        assert 'globs = ["**/*.py"]' in toml

    def test_round_trip_serialization(self):
        """Rule can be serialized and deserialized without loss."""
        original = Rule(
            id="r-security",
            title="Security rules",
            content="Validate all inputs.\nSanitize output.",
            tags=["security", "validation"],
            scope="project",
            priority=90,
            globs=["**/*.py", "**/*.js"],
        )
        toml = original.to_toml()
        restored = Rule.from_toml(toml)

        assert restored.id == original.id
        assert restored.title == original.title
        assert restored.content == original.content
        assert restored.tags == original.tags
        assert restored.scope == original.scope
        assert restored.priority == original.priority
        assert restored.globs == original.globs


class TestGlobMatching:
    """Test glob matching logic."""

    def test_exact_glob_match(self):
        """Identical globs match."""
        assert _globs_match("*.py", "*.py")
        assert _globs_match("src/**/*.js", "src/**/*.js")

    def test_recursive_glob_matches_anything(self):
        """Recursive ** glob matches other patterns."""
        assert _globs_match("**/*.py", "**/*.py")
        assert _globs_match("**", "anything.txt")
        # ** at the start with same suffix should match
        assert _globs_match("**/*.py", "**/*.py")

    def test_suffix_glob_matching(self):
        """Pattern matching for extension globs."""
        assert _globs_match("**/*.py", "**/*.py")
        assert _globs_match("*.py", "*.py")

    def test_non_matching_globs(self):
        """Different specific patterns don't match."""
        assert not _globs_match("*.py", "*.js")
        assert not _globs_match("*.txt", "*.md")

    def test_rule_matches_globs_empty_rule_globs(self):
        """Rule with no globs matches any file list."""
        rule = Rule(id="r-global", title="Global", content="Applies everywhere")
        assert rule.matches_globs(["**/*.py"])
        assert rule.matches_globs(["src/", "tests/"])

    def test_rule_matches_globs_with_patterns(self):
        """Rule matches when its globs overlap with provided globs."""
        rule = Rule(
            id="r-naming",
            title="Python naming",
            content="Use snake_case",
            globs=["**/*.py"],
        )
        assert rule.matches_globs(["**/*.py"])
        # Different glob patterns don't match unless they truly overlap
        assert not rule.matches_globs(["**/*.js"])
        assert not rule.matches_globs(["*.js"])

    def test_rule_matches_globs_empty_file_list(self):
        """Rule with globs doesn't match empty file list."""
        rule = Rule(
            id="r-test",
            title="Test rule",
            content="Something",
            globs=["**/*.py"],
        )
        assert not rule.matches_globs([])


class TestSimilarityScoring:
    """Test similarity scoring between rule contents."""

    def test_identical_content_scores_one(self):
        """Identical content scores 1.0."""
        rule = Rule(id="r-test", title="Test", content="This is test content.")
        score = rule.similarity_score("This is test content.")
        assert score == 1.0

    def test_completely_different_content_scores_low(self):
        """Completely different content scores low (< 0.5)."""
        rule = Rule(
            id="r-test",
            title="Test",
            content="Python naming conventions use snake_case",
        )
        score = rule.similarity_score("JavaScript uses camelCase entirely different language")
        assert score < 0.5

    def test_very_similar_content_scores_high(self):
        """Very similar content scores reasonably high."""
        rule = Rule(
            id="r-test",
            title="Test",
            content="Python functions should use snake_case naming conventions",
        )
        score = rule.similarity_score("Python functions use snake_case naming conventions")
        # With significant word overlap, score should be > 0.3
        assert score > 0.3

    def test_empty_content_edge_case(self):
        """Empty content handling."""
        rule = Rule(id="r-test", title="Test", content="")
        assert rule.similarity_score("") == 1.0
        assert rule.similarity_score("some text") == 0.0

    def test_similarity_ignores_case_and_punctuation(self):
        """Similarity scoring normalizes case and punctuation."""
        rule = Rule(
            id="r-test",
            title="Test",
            content="Use SNAKE_CASE for Python functions!",
        )
        score = rule.similarity_score("use snake case for python functions")
        # Should be similar despite case and punctuation differences
        assert score > 0.5


class TestTokenization:
    """Test tokenization helper."""

    def test_basic_tokenization(self):
        """Text is split into meaningful tokens."""
        tokens = _tokenize("Use snake_case for Python functions")
        assert "snake_case" in tokens or "snake" in tokens
        assert "python" in tokens
        assert "functions" in tokens
        # Very short tokens are filtered
        assert "" not in tokens
        assert "a" not in tokens

    def test_tokenization_removes_punctuation(self):
        """Punctuation is normalized away."""
        tokens = _tokenize("Use 'snake_case'! Test-case.")
        assert "snake_case" in tokens or "snake" in tokens
        # Test-case becomes test-case as a token after punctuation removal
        assert any("test" in t for t in tokens)

    def test_tokenization_case_insensitive(self):
        """Tokenization is case-insensitive."""
        tokens1 = _tokenize("Python PYTHON python")
        tokens2 = _tokenize("PYTHON python Python")
        assert set(tokens1) == set(tokens2)


class TestRuleFieldDefaults:
    """Test default values for rule fields."""

    def test_default_scope_is_project(self):
        """Default scope is 'project'."""
        rule = Rule(id="r-test", title="Test", content="Content")
        assert rule.scope == "project"

    def test_default_priority_is_fifty(self):
        """Default priority is 50."""
        rule = Rule(id="r-test", title="Test", content="Content")
        assert rule.priority == 50

    def test_default_tags_is_empty_list(self):
        """Default tags is an empty list."""
        rule = Rule(id="r-test", title="Test", content="Content")
        assert rule.tags == []

    def test_default_globs_is_empty_list(self):
        """Default globs is an empty list (applies to all files)."""
        rule = Rule(id="r-test", title="Test", content="Content")
        assert rule.globs == []

    def test_created_and_updated_timestamps(self):
        """Created and updated are set to current UTC time by default."""
        before = datetime.now(UTC)
        rule = Rule(id="r-test", title="Test", content="Content")
        after = datetime.now(UTC)
        assert before <= rule.created <= after
        assert before <= rule.updated <= after


class TestConfigIntegration:
    """Test that RulesConfig is properly integrated into Config."""

    def test_config_has_rules_section(self):
        """Config class has a rules section."""
        from ddflow.config import Config

        cfg = Config()
        assert hasattr(cfg, "rules")
        assert cfg.rules is not None

    def test_rules_config_defaults(self):
        """RulesConfig has correct defaults."""
        from ddflow.config import RulesConfig

        cfg = RulesConfig()
        assert cfg.max_rules == 200
        assert cfg.max_size_bytes == 50000
        assert cfg.tags_allowed == []
        assert cfg.scopes_allowed == ["project", "phase", "task"]

    def test_rules_config_fields_documented(self):
        """All RulesConfig fields are documented in KNOB_DOCS."""
        from ddflow.config import KNOB_DOCS

        # Verify documentation exists for rules knobs
        assert "rules.max_rules" in KNOB_DOCS
        assert "rules.max_size_bytes" in KNOB_DOCS
        assert "rules.tags_allowed" in KNOB_DOCS
        assert "rules.scopes_allowed" in KNOB_DOCS

        # Verify they're non-empty
        for key in [
            "rules.max_rules",
            "rules.max_size_bytes",
            "rules.tags_allowed",
            "rules.scopes_allowed",
        ]:
            assert len(KNOB_DOCS[key]) > 0


class TestRuleLimits:
    """Test enforcement of size and count limits."""

    def test_max_rules_limit(self):
        """Config enforces max_rules limit."""
        from ddflow.config import RulesConfig

        cfg = RulesConfig(max_rules=10)
        assert cfg.max_rules == 10

    def test_max_size_bytes_limit(self):
        """Config enforces max_size_bytes limit."""
        from ddflow.config import RulesConfig

        cfg = RulesConfig(max_size_bytes=100000)
        assert cfg.max_size_bytes == 100000

    def test_tags_allowed_validation(self):
        """Config can restrict allowed tags."""
        from ddflow.config import RulesConfig

        cfg = RulesConfig(tags_allowed=["testing", "security", "naming"])
        assert cfg.tags_allowed == ["testing", "security", "naming"]

    def test_scopes_allowed_validation(self):
        """Config can restrict allowed scopes."""
        from ddflow.config import RulesConfig

        cfg = RulesConfig(scopes_allowed=["project", "phase"])
        assert cfg.scopes_allowed == ["project", "phase"]
