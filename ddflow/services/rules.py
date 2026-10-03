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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib


def _now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


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
        if len(parts) == 2:
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
        if "content" not in data and not content:
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
        created = 2026-10-03T12:00:00
        updated = 2026-10-03T12:00:00

        Follow snake_case for Python functions...
        ```
        """
        from datetime import datetime as dt

        # Format timestamps as ISO strings if they are datetime objects
        created_str = (
            self.created.isoformat()
            if isinstance(self.created, dt)
            else str(self.created)
        )
        updated_str = (
            self.updated.isoformat()
            if isinstance(self.updated, dt)
            else str(self.updated)
        )

        lines = []
        lines.append(f'id = "{self.id}"')
        lines.append(f'title = "{self.title}"')
        if self.tags:
            tags_str = ', '.join(f'"{t}"' for t in self.tags)
            lines.append(f"tags = [{tags_str}]")
        lines.append(f'scope = "{self.scope}"')
        lines.append(f"priority = {self.priority}")
        if self.globs:
            globs_str = ', '.join(f'"{g}"' for g in self.globs)
            lines.append(f"globs = [{globs_str}]")
        lines.append(f'created = "{created_str}"')
        lines.append(f'updated = "{updated_str}"')

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


def _is_valid_rule_id(rule_id: str) -> bool:
    """Check if a rule id is valid (kebab-case with 'r-' prefix)."""
    if not rule_id.startswith("r-"):
        return False
    # After 'r-', must be kebab-case: lowercase letters, digits, and hyphens
    rest = rule_id[2:]
    return bool(re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", rest))


def _tokenize(text: str) -> list[str]:
    """Simple tokenization: split on whitespace and punctuation."""
    # Remove common punctuation and split on whitespace
    text = re.sub(r"[^\w\s-]", " ", text.lower())
    tokens = text.split()
    # Filter out very short tokens (noise)
    return [t for t in tokens if len(t) > 2]


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
        if suffix1 == suffix2 or suffix1 == "" or suffix2 == "":
            return True
        # Check if suffixes are exactly the same (e.g., "*.py" and "*.py")
        if suffix1 == suffix2:
            return True

    # ** by itself matches everything
    if pattern1 == "**" or pattern2 == "**":
        return True

    return False
