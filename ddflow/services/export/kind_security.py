"""The ``security`` document kind: a GitHub-compatible ``SECURITY.md`` (research R4f98e28a2a).

``collect(query)`` is the data: release lines from the version tags and known issues from the
recorded bugs, as plain values, kept apart from the writer so another document type can render
it. The template puts each in a named region, and the reporting section outside them is the
operator's: seeded once with a TODO when the file is created, never written again.

Nothing here is invented. A line's status comes from configuration, an issue from a recorded
bug, a version from a tag. What ddflow does not record (dependency advisories and the findings
of a security pass, which P-security-gate will add) is said to be absent.

Which bug counts: one carrying a ``[export.security].tags`` tag (default ``security``,
``secbug``), on itself or on its fix task; an invalid finding never does. A row holds the id
and the severity; ``disclose`` adds a title (``ids`` none, ``fixed`` fixed ones, ``all`` open
ones too), and the summary of a bug with no title stands in for it. Exploit detail is never
published by default, and the body is redacted like every export.

Which version holds a fix: every tag containing the fix task's landing, by ancestry, so a fix
on a maintenance line clears that line alone. A fix with no landing is placed by its date
against the tag dates, and the row says ``by date``, the weaker claim.

Which version is marked ``vulnerable``: the newest release of a line that does not hold the
fix, when it was tagged at or after the bug was found (the bug was there or nobody can say it
was not), and the newest release overall for any bug still unfixed in a release, which errs
towards a warning. A line last tagged before the bug was found is not marked: nothing is known.

There is deliberately no "as of": a document that changed with every event would churn on every
merge. It changes when a tag, a security bug or the configuration does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import Config
from ...core.model import Bug, Item
from . import kind_changelog as KC
from . import registry
from .frame import one_line
from .kind_bugs import first_sentence
from .query import Query

DEFAULT_TAGS = ("security", "secbug")
UNRELEASED = "the next release"  # landed, in no tag yet
UNKNOWN = "unknown"


@dataclass(frozen=True)
class _Fix:
    """Where one bug's fix stands: the tags holding it (indexes), and how that is known."""

    holding: frozenset[int] = frozenset()
    by_date: bool = False
    placed: bool = True

    def label(self, tags: list[KC._Tag]) -> str:
        if not self.placed:
            return UNKNOWN
        return tags[min(self.holding)].version if self.holding else UNRELEASED


def _fix_item(q: Query, b: Bug) -> Item | None:
    return q.item(b.fix_task) if b.fix_task else None


def _is_security(q: Query, b: Bug, wanted: set[str]) -> bool:
    if b.resolution == "invalid":
        return False
    item = _fix_item(q, b)
    return bool(wanted & set(b.tags)) or (item is not None and bool(wanted & set(item.tags)))


def _fixes(q: Query, cfg: Config, bugs: list[Bug], tags: list[KC._Tag]) -> dict[str, _Fix]:
    """Where each FIXED bug's fix stands, by landing when it has one, else by date."""
    repo = q.repo
    assert repo is not None
    fixed = [b for b in bugs if b.resolution == "fixed"]
    items = {b.id: _fix_item(q, b) for b in fixed}
    shas = {i.merged_sha for i in items.values() if i is not None and i.merged_sha}
    info = KC._known(repo, shas)
    second = {p[1] for _, p in info.values() if len(p) > 1}
    info.update(KC._known(repo, second - set(info)))
    KC._load_commits(repo, tags, shas | second)
    out: dict[str, _Fix] = {}
    for b in fixed:
        item = items[b.id]
        if item is not None and item.merged_sha in info:
            out[b.id] = _Fix(frozenset(KC.tags_holding(repo, cfg, item, tags, info)))
            continue
        out[b.id] = _by_date(b, tags)
    return out


def _by_date(b: Bug, tags: list[KC._Tag]) -> _Fix:
    """A fix with no landing: held by every tag made at or after it. Each tag is asked,
    because ``tags`` is in version order and a maintenance tag can be newer than a higher
    version, which must not be called fixed for it."""
    when = KC._stamp(b.fixed_at)
    if when is None or any(t.instant is None for t in tags):
        return _Fix(placed=False)
    held = (n for n, t in enumerate(tags) if t.instant is not None and when <= t.instant)
    return _Fix(frozenset(held), by_date=True)


def _covers(line: str, names: list[str]) -> bool:
    """``line`` (``1.2``) is named by ``1.2``, or by its major (``1``, a [flow.lines] name)."""
    return any(line == n or line.startswith(f"{n}.") for n in names)


def _status(line: str, newest: bool, cfg: Config) -> str:
    t = cfg.export.table("security")
    if _covers(line, list(t.get("deprecated_lines") or [])):
        return "deprecated"
    supported = list(t.get("supported_lines") or []) + list(cfg.flow.lines)
    return "supported" if newest or _covers(line, supported) else "unsupported"


def _carried(b: Bug, tag: KC._Tag) -> bool:
    found = KC._stamp(b.found_at)
    return found is not None and tag.instant is not None and found <= tag.instant


def _vulnerable(bugs: list[Bug], fixes: dict[str, _Fix], tags: list[KC._Tag], n: int) -> list[str]:
    """The ``vulnerable: <id>, ...`` marks of the release ``tags[n]``."""
    out = []
    for b in bugs:
        fix = fixes.get(b.id)
        if (fix is not None and n in fix.holding) or not (
            n == len(tags) - 1 or _carried(b, tags[n])
        ):
            continue
        state = f"fixed in {fix.label(tags)}" if fix else "not fixed yet"
        out.append(f"vulnerable: {b.id}, {state}")
    return out


def _versions(
    bugs: list[Bug], fixes: dict[str, _Fix], tags: list[KC._Tag], cfg: Config
) -> list[dict[str, Any]]:
    """One row per release line (``MAJOR.MINOR``), newest first, from its newest release."""
    rows: dict[str, dict[str, Any]] = {}
    for n in range(len(tags) - 1, -1, -1):
        t = tags[n]
        line = ".".join(t.version.split(".")[:2])
        if line not in rows:
            rows[line] = {
                "line": line,
                "status": _status(line, not rows, cfg),
                "latest": t.version,
                "released": t.date,
                "vulnerable": _vulnerable(bugs, fixes, tags, n),
            }
    return list(rows.values())


def _title(b: Bug) -> str:
    return one_line(b.title, 100) or first_sentence(b.summary)


def _issue(b: Bug, fix: _Fix | None, tags: list[KC._Tag], disclose: str) -> dict[str, Any]:
    shown = disclose == "all" or (disclose == "fixed" and fix is not None)
    return {
        "id": b.id,
        "severity": b.severity or "unrated",
        "scope": "" if b.scope == "project" else b.scope,
        "found": b.found_at[:10],
        "fixed": b.fixed_at[:10],
        "fixed_in": fix.label(tags) if fix else "",
        "by_date": fix.by_date if fix else False,
        "title": _title(b) if shown else "",
    }


def collect(q: Query) -> dict[str, Any]:
    """Supported lines and known issues as plain data: what any security document renders."""
    cfg = KC.repo_config(q, "the security document")
    assert q.repo is not None
    table = cfg.export.table("security")
    wanted = set(table.get("tags", DEFAULT_TAGS))
    disclose = str(table.get("disclose") or "ids")
    bugs = [b for b in q.bugs() if _is_security(q, b, wanted)]
    tags = KC._tags(q.repo, cfg.flow.tag_prefix)
    fixes = _fixes(q, cfg, bugs, tags)
    opened = sorted((b for b in bugs if b.open), key=lambda b: (b.found_at, b.id))
    closed = sorted((b for b in bugs if b.id in fixes), key=lambda b: (b.fixed_at, b.id))
    return {
        "versions": _versions(bugs, fixes, tags, cfg),
        "open": [_issue(b, None, tags, disclose) for b in opened],
        "fixed": [_issue(b, fixes[b.id], tags, disclose) for b in reversed(closed)],
    }


registry.register(
    registry.DocKind(
        name="security",
        default_target="SECURITY.md",
        data=lambda q, _f: collect(q),
        update_mode=registry.REGION,
        title="Security policy: supported versions and known issues",
        also_at=(".github/SECURITY.md", "docs/SECURITY.md"),
    )
)
