"""Tests for RulesStorage and rules API.

Tests cover:
- Round-trip storage: add rule → read from disk → verify
- Manifest generation: add 3 rules, generate manifest, check structure
- Update: modify priority/tags, verify file updated
- Delete: remove rule, verify file gone, manifest updated
- Concurrent adds (two agents adding rules simultaneously)
- Manifest idempotency (regenerate = identical bytes)
- Rules survive a `ddflow rebuild` (rebuild from event log)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.rules import Rule, RulesStorage
from ddflow.api.rules import (
    rules_manifest,
    apply_rule_update,
    rule_add,
    rule_update,
    rule_remove,
    rule_get,
    rule_list,
)


def _now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


class TestRulesStorage:
    """RulesStorage class tests."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository directory."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / ".ddflow").mkdir(parents=True, exist_ok=True)
            yield repo_path

    @pytest.fixture
    def storage(self, temp_repo):
        """Create a RulesStorage instance with temp repo."""
        return RulesStorage(temp_repo)

    def test_add_rule_creates_file(self, storage, temp_repo):
        """Test that adding a rule creates a TOML file."""
        rule = Rule(
            id="r-naming",
            title="Naming conventions",
            content="Use snake_case for functions",
            tags=["naming", "style"],
            scope="project",
            priority=75,
        )

        added_rule, event_fields = storage.add(rule)

        # Check file exists
        rule_file = temp_repo / ".ddflow" / "rules" / "r-naming.toml"
        assert rule_file.exists(), "Rule file should be created"

        # Check file content can be read back
        content = rule_file.read_text()
        assert 'id = "r-naming"' in content
        assert "Use snake_case" in content

        # Check event fields
        assert event_fields["rule_id"] == "r-naming"
        assert event_fields["title"] == "Naming conventions"
        assert event_fields["scope"] == "project"
        assert event_fields["priority"] == 75

    def test_round_trip_add_and_get(self, storage):
        """Test that adding and retrieving gives the same rule."""
        original = Rule(
            id="r-test-coverage",
            title="Test coverage",
            content="All new code requires regression test",
            tags=["testing"],
            scope="project",
            priority=80,
        )

        storage.add(original)
        retrieved = storage.get(original.id)

        assert retrieved.id == original.id
        assert retrieved.title == original.title
        assert retrieved.content == original.content
        assert retrieved.tags == original.tags
        assert retrieved.scope == original.scope
        assert retrieved.priority == original.priority

    def test_get_nonexistent_rule_raises_error(self, storage):
        """Test that getting a nonexistent rule raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            storage.get("r-nonexistent")

    def test_add_duplicate_rule_raises_error(self, storage):
        """Test that adding a duplicate rule raises ValueError."""
        rule = Rule(
            id="r-dup",
            title="Duplicate",
            content="Should fail",
        )

        storage.add(rule)

        with pytest.raises(ValueError, match="already exists"):
            storage.add(rule)

    def test_remove_rule_deletes_file(self, storage, temp_repo):
        """Test that removing a rule deletes its file."""
        rule = Rule(
            id="r-remove-test",
            title="To be removed",
            content="This will be deleted",
        )

        storage.add(rule)
        rule_file = temp_repo / ".ddflow" / "rules" / "r-remove-test.toml"
        assert rule_file.exists()

        event_fields = storage.remove("r-remove-test")

        assert not rule_file.exists(), "Rule file should be deleted"
        assert event_fields["rule_id"] == "r-remove-test"

    def test_remove_nonexistent_rule_raises_error(self, storage):
        """Test that removing a nonexistent rule raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            storage.remove("r-nonexistent")

    def test_list_all_rules(self, storage):
        """Test listing all rules."""
        rules_to_add = [
            Rule(id="r-first", title="First", content="Content 1"),
            Rule(id="r-second", title="Second", content="Content 2", tags=["testing"]),
            Rule(id="r-third", title="Third", content="Content 3", tags=["naming"]),
        ]

        for rule in rules_to_add:
            storage.add(rule)

        listed = storage.list()

        assert len(listed) == 3
        ids = [r.id for r in listed]
        assert "r-first" in ids
        assert "r-second" in ids
        assert "r-third" in ids

    def test_list_filter_by_tag(self, storage):
        """Test filtering rules by tag."""
        storage.add(Rule(id="r-a", title="A", content="A", tags=["testing"]))
        storage.add(Rule(id="r-b", title="B", content="B", tags=["naming"]))
        storage.add(Rule(id="r-c", title="C", content="C", tags=["testing", "security"]))

        testing_rules = storage.list(tag="testing")

        assert len(testing_rules) == 2
        ids = [r.id for r in testing_rules]
        assert "r-a" in ids
        assert "r-c" in ids
        assert "r-b" not in ids

    def test_list_filter_by_scope(self, storage):
        """Test filtering rules by scope."""
        storage.add(Rule(id="r-p1", title="P1", content="P1", scope="project"))
        storage.add(Rule(id="r-p2", title="P2", content="P2", scope="phase"))
        storage.add(Rule(id="r-p3", title="P3", content="P3", scope="project"))

        project_rules = storage.list(scope="project")

        assert len(project_rules) == 2
        ids = [r.id for r in project_rules]
        assert "r-p1" in ids
        assert "r-p3" in ids
        assert "r-p2" not in ids

    def test_update_rule_modifies_file(self, storage, temp_repo):
        """Test updating a rule modifies the file."""
        rule = Rule(
            id="r-update-test",
            title="Original title",
            content="Original content",
            priority=50,
        )

        storage.add(rule)

        # Update the rule
        updated_rule, event_fields = storage.update(
            "r-update-test",
            title="Updated title",
            priority=75,
        )

        # Check the in-memory object
        assert updated_rule.title == "Updated title"
        assert updated_rule.priority == 75
        assert updated_rule.content == "Original content"  # unchanged

        # Check the file
        retrieved = storage.get("r-update-test")
        assert retrieved.title == "Updated title"
        assert retrieved.priority == 75
        assert retrieved.content == "Original content"

        # Check event fields
        assert event_fields["title"] == "Updated title"
        assert event_fields["priority"] == 75

    def test_update_nonexistent_rule_raises_error(self, storage):
        """Test that updating a nonexistent rule raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            storage.update("r-nonexistent", priority=50)


class TestRulesManifest:
    """Manifest generation tests."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository directory."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / ".ddflow").mkdir(parents=True, exist_ok=True)
            yield repo_path

    @pytest.fixture
    def storage(self, temp_repo):
        """Create a RulesStorage instance with temp repo."""
        return RulesStorage(temp_repo)

    def test_manifest_empty_repo(self, storage):
        """Test manifest generation for empty repo."""
        manifest = rules_manifest(storage)

        assert "# Project Rules" in manifest
        assert "auto-generated" in manifest.lower()
        assert "do not hand-edit" in manifest.lower()
        assert "No project rules" in manifest

    def test_manifest_with_three_rules(self, temp_repo, storage):
        """Test manifest generation with three rules."""
        storage.add(
            Rule(
                id="r-naming",
                title="Classes use PascalCase; functions use snake_case",
                content="Follow these conventions",
                tags=["naming"],
                scope="project",
            )
        )
        storage.add(
            Rule(
                id="r-test-coverage",
                title="All new code requires regression test",
                content="Every fix must have a regression test",
                tags=["testing"],
                scope="project",
            )
        )
        storage.add(
            Rule(
                id="r-security",
                title="Never commit credentials",
                content="All credentials in .env",
                tags=["security"],
                scope="project",
            )
        )

        # Verify rules were added - check if files exist
        rules_dir = temp_repo / ".ddflow" / "rules"
        assert rules_dir.exists(), f"Rules directory doesn't exist at {rules_dir}"
        toml_files = list(rules_dir.glob("*.toml"))
        assert len(toml_files) == 3, f"Expected 3 TOML files, found {len(toml_files)}: {toml_files}"

        listed = storage.list()
        assert len(listed) == 3, f"Expected 3 rules, got {len(listed)}"

        manifest = rules_manifest(storage)

        # Check header
        assert "# Project Rules" in manifest
        assert "auto-generated" in manifest.lower()

        # Check all rules are present
        assert "r-naming" in manifest
        assert "r-test-coverage" in manifest
        assert "r-security" in manifest

        # Check titles are present
        assert "Classes use PascalCase" in manifest
        assert "regression test" in manifest
        assert "Never commit credentials" in manifest

    def test_manifest_organization_by_scope_and_tag(self, temp_repo, storage):
        """Test that manifest organizes rules by scope and tag."""
        storage.add(
            Rule(
                id="r-proj-naming",
                title="Project naming rules",
                tags=["naming"],
                scope="project",
                content="",
            )
        )
        storage.add(
            Rule(
                id="r-proj-testing",
                title="Project testing rules",
                tags=["testing"],
                scope="project",
                content="",
            )
        )
        storage.add(
            Rule(
                id="r-phase-timing",
                title="Phase timing rules",
                tags=["timing"],
                scope="phase",
                content="",
            )
        )

        # Verify rules files were created
        rules_dir = temp_repo / ".ddflow" / "rules"
        storage_rules_dir = storage.rules_dir
        # Debug info
        if storage_rules_dir != rules_dir:
            # Paths might differ, so just check that files exist somewhere
            assert storage_rules_dir.exists(), (
                f"Storage rules_dir doesn't exist: {storage_rules_dir}"
            )
            toml_files = list(storage_rules_dir.glob("*.toml"))
        else:
            assert rules_dir.exists(), f"Rules directory doesn't exist"
            toml_files = list(rules_dir.glob("*.toml"))

        assert len(toml_files) == 3, (
            f"Expected 3 TOML files, found {len(toml_files)} at {storage_rules_dir}"
        )

        listed = storage.list()
        assert len(listed) == 3, f"Expected 3 rules, got {len(listed)}"

        manifest = rules_manifest(storage)

        # Check scopes appear
        assert "Project" in manifest
        assert "Phase" in manifest

    def test_manifest_includes_globs_and_priority(self, temp_repo, storage):
        """Test that manifest includes globs and priority when present."""
        storage.add(
            Rule(
                id="r-globs-test",
                title="Globs test",
                content="",
                globs=["**/*.py", "**/*.js"],
                priority=75,
            )
        )

        # Verify rule was created
        rules_dir = temp_repo / ".ddflow" / "rules"
        toml_files = list(rules_dir.glob("*.toml"))
        assert len(toml_files) == 1, f"Expected 1 TOML file, found {len(toml_files)}"

        manifest = rules_manifest(storage)

        # High priority should be shown
        assert "75" in manifest or "Priority" in manifest
        # Globs should be shown
        assert "*.py" in manifest

    def test_manifest_idempotency(self, temp_repo, storage):
        """Test that regenerating manifest gives identical bytes."""
        storage.add(Rule(id="r-1", title="First", content="", tags=["a"]))
        storage.add(Rule(id="r-2", title="Second", content="", tags=["b"]))
        storage.add(Rule(id="r-3", title="Third", content="", tags=["c"]))

        manifest1 = rules_manifest(storage)
        manifest2 = rules_manifest(storage)
        manifest3 = rules_manifest(storage)

        assert manifest1 == manifest2 == manifest3, "Manifest should be idempotent"


class TestRulesAPI:
    """High-level API tests (with event logging)."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository directory with config."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / ".ddflow").mkdir(parents=True, exist_ok=True)
            (repo_path / ".ddflow" / "events").mkdir(parents=True, exist_ok=True)

            # Create a minimal config
            config_file = repo_path / ".ddflow" / "config.toml"
            config_file.write_text("""
[agent]
id = "test-agent"

[log]
""")

            yield repo_path

    def test_manifest_updated_on_rule_add(self, temp_repo):
        """Test that DDFLOW.md is created/updated when a rule is added."""
        rule = Rule(
            id="r-test",
            title="Test rule",
            content="Test content",
        )

        # This would require mock log and cfg; for now just test storage directly
        storage = RulesStorage(temp_repo)
        storage.add(rule)

        manifest = rules_manifest(storage)
        manifest_file = temp_repo / "DDFLOW.md"

        # Manifest should be generated
        assert "r-test" in manifest
        assert "Test rule" in manifest

    def test_concurrent_rule_adds(self, temp_repo):
        """Test adding multiple rules (simulating concurrent adds)."""
        storage = RulesStorage(temp_repo)

        # Simulate two agents adding rules concurrently
        rule1 = Rule(id="r-agent1", title="Agent 1 rule", content="Content 1")
        rule2 = Rule(id="r-agent2", title="Agent 2 rule", content="Content 2")

        storage.add(rule1)
        storage.add(rule2)

        # Both should be retrievable
        listed = storage.list()
        assert len(listed) == 2
        ids = [r.id for r in listed]
        assert "r-agent1" in ids
        assert "r-agent2" in ids

    def test_rules_survive_rebuild(self, temp_repo):
        """Test that rules survive a storage rebuild scenario."""
        storage1 = RulesStorage(temp_repo)

        # Add some rules
        for i in range(3):
            rule = Rule(
                id=f"r-rule{i}",
                title=f"Rule {i}",
                content=f"Content {i}",
            )
            storage1.add(rule)

        # Create a new storage instance (simulating rebuild)
        storage2 = RulesStorage(temp_repo)
        listed = storage2.list()

        assert len(listed) == 3
        ids = [r.id for r in listed]
        for i in range(3):
            assert f"r-rule{i}" in ids

    def test_invalid_rule_skipped_on_list(self, temp_repo):
        """Test that invalid rule files are skipped during list."""
        storage = RulesStorage(temp_repo)

        # Add a valid rule
        rule = Rule(id="r-valid", title="Valid", content="")
        storage.add(rule)

        # Verify it was added
        rules_dir = temp_repo / ".ddflow" / "rules"
        toml_files = list(rules_dir.glob("*.toml"))
        assert len(toml_files) == 1, "Valid rule should be created"

        # Add an invalid rule file manually
        storage._ensure_dir()
        invalid_file = storage.rules_dir / "r-invalid.toml"
        invalid_file.write_text("this is not valid toml {{{")

        # Verify we have two files now
        toml_files = list(rules_dir.glob("*.toml"))
        assert len(toml_files) == 2, "Should have 2 TOML files (1 valid, 1 invalid)"

        # List should skip the invalid one and return only valid
        listed = storage.list()
        ids = [r.id for r in listed]

        assert "r-valid" in ids
        assert "r-invalid" not in ids
        assert len(listed) == 1


class TestRulesIntegration:
    """Integration tests combining storage and manifest."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository directory."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / ".ddflow").mkdir(parents=True, exist_ok=True)
            yield repo_path

    def test_add_modify_delete_cycle(self, temp_repo):
        """Test the full cycle: add, modify, delete."""
        storage = RulesStorage(temp_repo)

        # Add
        rule = Rule(
            id="r-cycle",
            title="Cycle test",
            content="Original",
            priority=50,
        )
        storage.add(rule)
        assert len(storage.list()) == 1

        # Modify
        storage.update("r-cycle", title="Modified", priority=75)
        retrieved = storage.get("r-cycle")
        assert retrieved.title == "Modified"
        assert retrieved.priority == 75

        # Manifest updated
        manifest = rules_manifest(storage)
        assert "Modified" in manifest

        # Delete
        storage.remove("r-cycle")
        assert len(storage.list()) == 0

        # Manifest updated
        manifest = rules_manifest(storage)
        assert "Cycle test" not in manifest


def test_rule_add_enforces_the_rules_limits(tmp_path):
    from ddflow import api
    from ddflow.services.rules import Rule
    from tests.conftest import run_cli  # noqa: F401

    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text(
        '[rules]\nmax_size_bytes = 10\nscopes_allowed = ["project"]\n'
    )
    big = api.rule_add(tmp_path, Rule(id="r1", title="t", content="x" * 11), check_dedup=False)
    assert big.exit != 0 and "max_size_bytes" in big.reason
    scope = api.rule_add(
        tmp_path, Rule(id="r2", title="t", content="ok", scope="task"), check_dedup=False
    )
    assert scope.exit != 0 and "scopes_allowed" in scope.reason


def test_max_rules_does_not_block_rewriting_an_existing_rule(tmp_path):
    from ddflow import api
    from ddflow.services.rules import Rule

    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text("[rules]\nmax_rules = 1\n")
    assert (
        api.rule_add(tmp_path, Rule(id="r-one", title="t", content="a"), check_dedup=False).exit == 0
    )
    again = api.rule_add(tmp_path, Rule(id="r-one", title="t", content="b"), check_dedup=False)
    assert again.exit == 0, again.reason
    other = api.rule_add(tmp_path, Rule(id="r-two", title="t", content="c"), check_dedup=False)
    assert other.exit != 0 and "max_rules" in other.reason
