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

A rule is one kind of guidance (`services.guidance`, shared with decisions): its file
format, storage, applicability, limits and similarity are the engine's, and `Rule` keeps
the user-facing name and the fields its callers know.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core import clock
from .guidance import fileformat, similarity
from .guidance.kinds import RULE, is_valid_rule_id
from .guidance.record import GuidanceRecord
from .guidance.store import GuidanceFiles

#: Kept under its old name: callers and tests import the validator from here.
_is_valid_rule_id = is_valid_rule_id


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
    #: What the file carried beyond the fields above (``categories``, ``owner``, ...): kept
    #: so that editing a rule writes back what a person put in it.
    record: GuidanceRecord | None = field(default=None, repr=False, compare=False)

    # -- The shared record --------------------------------------------------------

    def to_record(self) -> GuidanceRecord:
        """This rule as guidance: its text is the body, its globs the scope."""
        base = self.record or GuidanceRecord(
            id=self.id, kind=RULE.kind, enforcement=RULE.default_enforcement
        )
        return replace(
            base,
            id=self.id,
            title=self.title,
            body=self.content,
            scope=replace(base.scope, globs=tuple(self.globs)),
            level=self.scope,
            tags=self.tags,
            priority=self.priority,
            provenance={"created": self.created, "updated": self.updated},
        )

    @classmethod
    def from_record(cls, rec: GuidanceRecord) -> Rule:
        return cls(
            id=rec.id,
            title=rec.title,
            content=rec.body,
            tags=rec.tags,
            scope=rec.level,
            priority=rec.priority,
            globs=list(rec.scope.globs),
            created=rec.provenance["created"],
            updated=rec.provenance["updated"],
            record=rec,
        )

    # -- Validation & Storage --------------------------------------------------

    @classmethod
    def from_toml(cls, toml_text: str) -> Rule:
        """Parse a rule from TOML text with frontmatter (`guidance.fileformat` is the format).

        Raises ValueError if required fields are missing or invalid.
        """
        return cls.from_record(fileformat.parse(toml_text, RULE))

    def to_toml(self) -> str:
        """Serialize the rule to TOML format with frontmatter and content block."""
        return fileformat.render(self.to_record(), RULE)

    def similarity_score(self, other_content: str) -> float:
        """How alike this rule's content and ``other_content`` read: 0.0 (nothing shared)
        to 1.0 (identical), by `guidance.similarity.jaccard`."""
        return similarity.jaccard(self.content, other_content)


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
        self.files = GuidanceFiles(self.repo, RULE)
        self.rules_dir = self.files.directory

    def _ensure_dir(self) -> None:
        """Create the .ddflow/rules directory if it doesn't exist."""
        self.files.ensure_dir()

    def add(self, rule: Rule) -> tuple[Rule, dict[str, Any]]:
        """Add a new rule to storage.

        Returns:
            Tuple of (rule, event_fields) where event_fields contains
            the data to append to the event log
        """
        self.files.write(rule.to_record(), new=True)
        return rule, {
            "rule_id": rule.id,
            "title": rule.title,
            "scope": rule.scope,
            "tags": rule.tags,
            "priority": rule.priority,
        }

    def remove(self, rule_id: str) -> dict[str, Any]:
        """Remove a rule from storage.

        Returns:
            Event fields for the deletion event
        """
        self.files.delete(rule_id)
        return {"rule_id": rule_id}

    def get(self, rule_id: str) -> Rule:
        """Get a rule from storage.

        Raises:
            FileNotFoundError if the rule doesn't exist
        """
        return Rule.from_record(self.files.read(rule_id))

    def list(self, tag: str | None = None, scope: str | None = None) -> list[Rule]:
        """List all rules, optionally filtered by tag or scope (a file that does not
        load is left out)."""
        rules = []
        for rec in self.files.all():
            rule = Rule.from_record(rec)
            if tag and tag not in rule.tags:
                continue
            if scope and rule.scope != scope:
                continue
            rules.append(rule)
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
        for key, value in fields.items():
            if hasattr(rule, key):
                setattr(rule, key, value)
        rule.updated = _now()
        self.files.write(rule.to_record())
        event_fields = {"rule_id": rule_id}
        event_fields.update(fields)
        return rule, event_fields
