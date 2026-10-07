"""Rule schema and storage — foundational Rule dataclass for ddflow projects.

Rules are project-specific patterns, guidelines, or constraints encoded in the
queue. They have:

- An id (unique, kebab-case with "r-" prefix, e.g., "r-naming" or "r-test-coverage")
- A title (short description)
- Tags for categorization (naming, testing, security, etc.)
- A scope (where they apply: project, phase, task, global)
- A priority (0-100, higher = more important)
- File globs they govern (optional)
- The rule text itself

Rules are stored as TOML with frontmatter and can be queried and scored for
similarity. This module is the foundation that other Phase 1 tasks build on.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core import clock, textsim
from ..infra.fsio import replace_text
from ..infra.tomlcfg import basic_string

_FRONTMATTER_PARTS = 2  # frontmatter + content, split on the first blank line
_MIN_TOKEN_LEN = 2  # tokens this short are noise


def _now() -> datetime:
    """The current UTC time, timezone-aware: core.clock's, looked up at call time."""
    return clock.now_utc()


@dataclass
class Rule:
    """A project rule with metadata, scope, and content."""

    id: str
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    scope: str = "project"
    priority: int = 50
    globs: list[str] = field(default_factory=list)
    created: datetime = field(default_factory=_now)
    updated: datetime = field(default_factory=_now)

    # -- Validation & Storage --------------------------------------------------

    @classmethod
    def from_toml(cls, toml_text: str) -> Rule:
        """Parse a rule from TOML text with frontmatter.

        Format:
        ```
        id = "r-naming"
        title = "Naming conventions"
        tags = ["naming", "style"]
        scope = "project"
        priority = 75
        globs = ["**/*.py", "**/*.js"]

        # Rule content follows the frontmatter
        Follow snake_case for Python functions...
        ```

        Raises ValueError if required fields are missing or invalid.
        """
        lines = toml_text.split("\n", 1)
        if not lines:
            raise ValueError("Empty rule text")

        # Find the frontmatter boundary (first blank line or first content line)
        parts = toml_text.split("\n\n", 1)
        if len(parts) == _FRONTMATTER_PARTS:
            frontmatter_text, content = parts
        else:
            # No content block found; all is frontmatter
            frontmatter_text = toml_text
            content = ""

        try:
            data = tomllib.loads(frontmatter_text)
        except Exception as exc:
            raise ValueError(f"Invalid TOML in rule frontmatter: {exc}") from exc

        # Validate required fields
        if "id" not in data:
            raise ValueError("Rule must have an 'id' field")
        if "title" not in data:
            raise ValueError("Rule must have a 'title' field")
        # Content can be empty, but must be present either in data dict or as a content block
        # If the text has a \n\n separator, there's a content block (even if empty)
        has_content_in_data = "content" in data
        has_content_block = len(parts) == _FRONTMATTER_PARTS  # There was a \n\n separator
        if not has_content_in_data and not has_content_block:
            raise ValueError("Rule must have a 'content' field or content block")

        rule_id = data.get("id", "")
        if not _is_valid_rule_id(rule_id):
            raise ValueError(
                f"Invalid rule id '{rule_id}': must be kebab-case with 'r-' prefix (e.g., 'r-naming')"
            )

        # Use content from data dict if present, otherwise from content block
        rule_content = data.pop("content", content)

        now = _now()
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            content=rule_content.strip(),
            tags=data.get("tags", []),
            scope=data.get("scope", "project"),
            priority=int(data.get("priority", 50)),
            globs=data.get("globs", []),
            created=data.get("created", now),
            updated=data.get("updated", now),
        )

    def to_toml(self) -> str:
        """Serialize the rule to TOML format with frontmatter and content block.

        Returns:
        ```
        id = "r-naming"
        title = "Naming conventions"
        tags = ["naming", "style"]
        scope = "project"
        priority = 75
        globs = ["**/*.py", "**/*.js"]
        created = "2026-10-03T12:00:00"
        updated = "2026-10-03T12:00:00"

        Follow snake_case for Python functions...
        ```
        """
        from datetime import datetime as dt

        # Format timestamps as ISO strings if they are datetime objects
        created_str = (
            self.created.isoformat() if isinstance(self.created, dt) else str(self.created)
        )
        updated_str = (
            self.updated.isoformat() if isinstance(self.updated, dt) else str(self.updated)
        )

        lines = []
        lines.append(f"id = {_toml_str(self.id)}")
        lines.append(f"title = {_toml_str(self.title)}")
        if self.tags:
            lines.append(f"tags = [{', '.join(_toml_str(t) for t in self.tags)}]")
        lines.append(f"scope = {_toml_str(self.scope)}")
        lines.append(f"priority = {self.priority}")
        if self.globs:
            lines.append(f"globs = [{', '.join(_toml_str(g) for g in self.globs)}]")
        lines.append(f"created = {_toml_str(created_str)}")
        lines.append(f"updated = {_toml_str(updated_str)}")

        frontmatter = "\n".join(lines)
        return f"{frontmatter}\n\n{self.content}"

    def matches_globs(self, file_globs: list[str]) -> bool:
        """Check if any of this rule's globs match any in the provided list.

        Uses simple glob matching: checks if any glob pattern from this rule
        matches any file glob in the provided list. Empty globs means the rule
        applies to all files.

        Args:
            file_globs: List of file globs to check against

        Returns:
            True if rule has no globs (applies everywhere) or if any rule glob
            matches any file glob in the list.
        """
        if not self.globs:
            return True
        if not file_globs:
            return False

        # Simple glob matching: convert globs to regex patterns
        for rule_glob in self.globs:
            for file_glob in file_globs:
                if _globs_match(rule_glob, file_glob):
                    return True
        return False

    def similarity_score(self, other_content: str) -> float:
        """Compute similarity between this rule's content and other content.

        Uses a simple token-based similarity score (Jaccard index / textsim variant).
        Returns a score from 0.0 (completely different) to 1.0 (identical).

        Args:
            other_content: Content to compare against

        Returns:
            Similarity score between 0.0 and 1.0
        """
        if not self.content or not other_content:
            return 1.0 if self.content == other_content else 0.0

        # Tokenize by words
        self_tokens = set(_tokenize(self.content))
        other_tokens = set(_tokenize(other_content))

        if not self_tokens or not other_tokens:
            return 1.0 if self.content == other_content else 0.0

        # Jaccard similarity: intersection / union
        intersection = len(self_tokens & other_tokens)
        union = len(self_tokens | other_tokens)

        if union == 0:
            return 0.0
        return intersection / union


# -- Helpers ------------------------------------------------------------------


def _toml_str(value: str) -> str:
    """`value` as a TOML basic string (B28cab0652a): the shared writer."""
    return basic_string(value)


def _is_valid_rule_id(rule_id: str) -> bool:
    """Check if a rule id is valid (kebab-case with 'r-' prefix)."""
    if not rule_id.startswith("r-"):
        return False
    # After 'r-', must be kebab-case: lowercase letters, digits, and hyphens
    rest = rule_id[2:]
    return bool(re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", rest))


def _tokenize(text: str) -> list[str]:
    """Lowercase words longer than ``_MIN_TOKEN_LEN``, hyphens kept inside a word."""
    return textsim.words(text, min_len=_MIN_TOKEN_LEN + 1, hyphens=True, fold=True)


def _globs_match(pattern1: str, pattern2: str) -> bool:
    """Check if two glob patterns overlap or are identical.

    Simple matching: checks for exact match or if patterns are truly compatible.
    This is a basic implementation; a full glob matcher would use fnmatch.
    """
    # Exact match
    if pattern1 == pattern2:
        return True

    # If both contain **, they might overlap
    if pattern1.startswith("**") and pattern2.startswith("**"):
        # Both are recursive, check suffix overlap
        suffix1 = pattern1[2:].lstrip("/")
        suffix2 = pattern2[2:].lstrip("/")
        if suffix1 in (suffix2, "") or suffix2 == "":
            return True
        # Check if suffixes are exactly the same (e.g., "*.py" and "*.py")
        if suffix1 == suffix2:
            return True

    # ** by itself matches everything
    if pattern1 == "**" or pattern2 == "**":
        return True

    return False


# -- Rules Storage --------------------------------------------------------


class RulesStorage:
    """Persist and retrieve rules from .ddflow/rules/ directory.

    All operations record events to the event log.
    """

    def __init__(self, repo: Path):
        """Initialize storage for the given repository.

        Args:
            repo: Path to the repository root (where .ddflow/ exists)
        """
        self.repo = Path(repo)
        self.rules_dir = self.repo / ".ddflow" / "rules"

    def _ensure_dir(self) -> None:
        """Create the .ddflow/rules directory if it doesn't exist."""
        self.rules_dir.mkdir(parents=True, exist_ok=True)

    def _rule_path(self, rule_id: str) -> Path:
        """Get the path for a rule's TOML file.

        Args:
            rule_id: The rule ID (e.g., "r-naming")

        Returns:
            Path to the rule's TOML file
        """
        return self.rules_dir / f"{rule_id}.toml"

    def add(self, rule: Rule) -> tuple[Rule, dict[str, Any]]:
        """Add a new rule to storage.

        Args:
            rule: The Rule to add

        Returns:
            Tuple of (rule, event_fields) where event_fields contains
            the data to append to the event log
        """
        self._ensure_dir()
        rule_path = self._rule_path(rule.id)

        if rule_path.exists():
            raise ValueError(f"Rule {rule.id} already exists at {rule_path}")

        # Write the rule to disk
        replace_text(rule_path, rule.to_toml())

        # Return the rule and event fields
        return rule, {
            "rule_id": rule.id,
            "title": rule.title,
            "scope": rule.scope,
            "tags": rule.tags,
            "priority": rule.priority,
        }

    def remove(self, rule_id: str) -> dict[str, Any]:
        """Remove a rule from storage.

        Args:
            rule_id: The rule ID to remove

        Returns:
            Event fields for the deletion event
        """
        rule_path = self._rule_path(rule_id)

        if not rule_path.exists():
            raise ValueError(f"Rule {rule_id} not found at {rule_path}")

        # Delete the file
        rule_path.unlink()

        return {"rule_id": rule_id}

    def get(self, rule_id: str) -> Rule:
        """Get a rule from storage.

        Args:
            rule_id: The rule ID to retrieve

        Returns:
            The Rule

        Raises:
            FileNotFoundError if the rule doesn't exist
        """
        rule_path = self._rule_path(rule_id)

        if not rule_path.exists():
            raise FileNotFoundError(f"Rule {rule_id} not found at {rule_path}")

        toml_text = rule_path.read_text("utf-8")
        return Rule.from_toml(toml_text)

    def list(self, tag: str | None = None, scope: str | None = None) -> list[Rule]:
        """List all rules, optionally filtered by tag or scope.

        Args:
            tag: Optional tag to filter by
            scope: Optional scope to filter by

        Returns:
            List of matching Rule objects
        """
        self._ensure_dir()
        rules = []

        for toml_file in sorted(self.rules_dir.glob("*.toml")):
            try:
                rule = self.get(toml_file.stem)
                # Apply filters
                if tag and tag not in rule.tags:
                    continue
                if scope and rule.scope != scope:
                    continue
                rules.append(rule)
            except Exception:
                # Skip invalid rules
                continue

        return rules

    def update(self, rule_id: str, **fields: Any) -> tuple[Rule, dict[str, Any]]:
        """Update an existing rule in storage.

        Only the specified fields are updated; other fields retain their values.

        Args:
            rule_id: The rule ID to update
            **fields: Fields to update (e.g., priority=75, tags=["naming"])

        Returns:
            Tuple of (updated_rule, event_fields)

        Raises:
            FileNotFoundError if the rule doesn't exist
        """
        rule = self.get(rule_id)

        # Update the rule with new values
        for key, value in fields.items():
            if hasattr(rule, key):
                setattr(rule, key, value)

        # Update the timestamp
        rule.updated = _now()

        # Write back to disk
        rule_path = self._rule_path(rule_id)
        replace_text(rule_path, rule.to_toml())

        # Return the updated rule and event fields (only changed fields)
        event_fields = {"rule_id": rule_id}
        event_fields.update(fields)

        return rule, event_fields
