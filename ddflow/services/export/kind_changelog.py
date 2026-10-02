"""The ``changelog`` document kind: ``CHANGELOG.md`` in Keep a Changelog 1.1 form, from the
event log and the repository's version tags (decision D-export, 4).

Layout: ``## [Unreleased]`` then ``## [x.y.z] - date``, newest first, each with the sections
Added / Changed / Deprecated / Removed / Fixed / Security (empty ones left out), then
compare links built from the tags and the ``origin`` remote.

What counts as an entry: a finished (``done``) task, and a fixed bug that carries an explicit
``changelog`` line and is not already named by a listed task's ``(fixes bug X)`` suffix.
An entry whose field says ``skip`` / ``internal`` is left out.

Category, first rule that answers:

1. the explicit ``changelog`` field's category;
2. the Conventional Commit type of the item's landing commit (the merge commit's subject,
   else its merged branch tip's): ``feat`` Added, ``fix`` Fixed, ``security`` Security,
   ``deprecate`` Deprecated, ``perf``/``refactor``/``revert`` or a ``!`` Changed;
3. the item's tags as ``version cut`` reads them (``services/flow.py`` ``item_bump``):
   breaking Changed, bugfix/hotfix Fixed, feature Added;
4. a ``(fixes bug X)`` title suffix: Fixed;
5. Changed.

Line: the field's line, else the title with the ``(fixes bug X)`` suffix removed, first
sentence only.

Version of an entry: the OLDEST version tag whose history contains the item's landing
(``reached()`` in ``services/flow.py``, so a back-merged hotfix is found); none: Unreleased.
An item with no ``merged_sha`` (or one this repository does not have), and every bug, is
placed by its completion / fix time against the tag dates instead, and the entry says so
(``by_date``) -- a weaker claim that the document never presents as git ancestry. Nothing is
reconstructed at or before the FIRST version tag: it is the baseline, has no section, and
everything it contains stays out. With no tag at all there is no baseline, and everything
finished is Unreleased.

``tag`` filter: ``--version X`` of the surface. ``X`` (``1.2.0`` or ``v1.2.0``) renders that
one version's section without the file header (release notes); ``unreleased`` renders only the
Unreleased section, which is the body to put in a hand-kept CHANGELOG's marker region
(``write.write_region``) -- ``append_unreleased`` is the matching append-mode producer.

This kind reads git through ``Query.repo`` (a second sanctioned read beside the configuration
the ``rules`` kind reads): git that cannot be read is ``ExportError`` exit 2, never an empty
clean changelog. The data function itself stays deterministic: dates come from git tags and
log records, never the clock.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...core import flow as F
from ...core.events import CHANGELOG_CATEGORIES
from ...core.model import DONE, Item
from ...infra import worktree as W
from ..flow import reached
from . import registry
from .frame import one_line
from .query import EXIT_UNAVAILABLE, ExportError, Query, _parse_ts

UNRELEASED = "unreleased"
LINE_CHARS = 200
_FIXES = re.compile(r"\s*\(fixes bugs? ([^)]*)\)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_BATCH = 200

#: Conventional Commit type -> Keep a Changelog category. Other types (docs, chore, test,
#: ci, build, style) say nothing about the change, so the next rule decides.
_CC_CATEGORY = {
    "feat": "Added",
    "fix": "Fixed",
    "security": "Security",
    "deprecate": "Deprecated",
    "perf": "Changed",
    "refactor": "Changed",
    "revert": "Changed",
}
_BUMP_CATEGORY = {F.MAJOR: "Changed", F.PATCH: "Fixed", F.MINOR: "Added"}


def _git(repo: Path, *args: str) -> str:
    """Stdout of a git command; failure is ``ExportError`` (could not run)."""
    try:
        r = W.git(repo, *args)
    except (OSError, ValueError) as exc:
        raise ExportError(f"could not run git: {exc}", EXIT_UNAVAILABLE) from exc
    except Exception as exc:  # a timeout from the subprocess layer
        raise ExportError(
            f"could not run git: {type(exc).__name__}: {exc}", EXIT_UNAVAILABLE
        ) from exc
    if not r.ok:
        raise ExportError(f"git {' '.join(args[:2])} failed: {r.err or r.out}", EXIT_UNAVAILABLE)
    return r.out


class _Tag:
    __slots__ = ("commits", "date", "instant", "name", "version")

    def __init__(self, name: str, version: str, instant: datetime | None, date: str) -> None:
        self.name, self.version, self.instant, self.date = name, version, instant, date
        self.commits: set[str] = set()


def _tags(repo: Path, prefix: str) -> list[_Tag]:
    """Version tags, OLDEST version first, with their dates and the commits each contains."""
    _git(repo, "rev-parse", "--git-dir")
    out = _git(
        repo,
        "for-each-ref",
        "refs/tags",
        "--format=%(refname:short)\t%(creatordate:iso-strict)",
    )
    when: dict[str, str] = {}
    for line in out.splitlines():
        name, _, stamp = line.partition("\t")
        when[name.strip()] = stamp.strip()
    tags: list[_Tag] = []
    for name, version in reversed(W.version_tags(repo, prefix)):  # oldest first
        stamp = when.get(name, "")
        try:
            instant: datetime | None = _parse_ts(stamp)
        except ValueError:
            instant = None
        tags.append(
            _Tag(
                name,
                version,
                instant,
                instant.astimezone(UTC).date().isoformat() if instant else "",
            )
        )
    return tags


def _load_commits(repo: Path, tags: list[_Tag], wanted: set[str]) -> None:
    """Fill each tag's ``commits`` with the WANTED shas its history contains (a landing
    commit or its merged branch tip), so memory is bounded by the items, not the history."""
    for t in tags:
        t.commits = wanted & set(_git(repo, "rev-list", t.name).split())


def _subjects(repo: Path, shas: set[str]) -> dict[str, tuple[str, list[str]]]:
    """sha -> (subject, parents) for the commits git knows of (unknown ones are absent)."""
    out: dict[str, tuple[str, list[str]]] = {}
    todo = sorted(shas)
    for i in range(0, len(todo), _BATCH):
        r = W.git(
            repo,
            "log",
            "--no-walk=unsorted",
            "--format=%H%x09%P%x09%s",
            *todo[i : i + _BATCH],
            "--",
        )
        if not r.ok:
            continue  # an unknown sha in the batch: its commits stay unknown below
        for line in r.out.splitlines():
            sha, _, rest = line.partition("\t")
            parents, _, subject = rest.partition("\t")
            out[sha] = (subject, parents.split())
    return out


def _known(repo: Path, shas: set[str]) -> dict[str, tuple[str, list[str]]]:
    """``_subjects`` that survives a batch holding a sha this repository lacks."""
    out = _subjects(repo, shas)
    for sha in sorted(shas - set(out)):
        if W.rev(repo, sha):
            out.update(_subjects(repo, {sha}))
    return out


def _cc_category(subject: str) -> str:
    m = F._CC.match(subject or "")
    if not m:
        return ""
    if m.group("bang"):
        return "Changed"
    return _CC_CATEGORY.get(m.group("type").lower(), "")


def _first_sentence(text: str) -> str:
    t = " ".join((text or "").split())
    t = _SENTENCE_END.split(t, maxsplit=1)[0]
    return t.rstrip(".").strip()


def _fixed_bugs(title: str) -> list[str]:
    ids: list[str] = []
    for m in _FIXES.finditer(title or ""):
        ids += [b.strip() for b in re.split(r",|\band\b", m.group(1)) if b.strip()]
    return ids


def _stamp(text: str) -> datetime | None:
    try:
        return _parse_ts(text)
    except ValueError:
        return None


def _by_date(tags: list[_Tag], at: str) -> int | None:
    """Index of the first tag made at or after ``at``; ``len(tags)`` for later than every
    tag (Unreleased); None if ``at`` or a tag date cannot be read."""
    when = _stamp(at)
    if when is None or any(t.instant is None for t in tags):
        return None
    for i, t in enumerate(tags):
        assert t.instant is not None
        if when <= t.instant:
            return i
    return len(tags)


def _remote_base(repo: Path) -> str:
    """``https://host/owner/repo`` from ``origin``, "" when there is none worth linking."""
    r = W.git(repo, "remote", "get-url", "origin")
    url = r.out.strip() if r.ok else ""
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", url) or re.match(
        r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", url
    )
    return f"https://{m.group(1)}/{m.group(2)}" if m else ""


def _entries(q: Query, cfg: Any, tags: list[_Tag]) -> tuple[list[dict[str, Any]], int]:
    """Every changelog entry with its section index (0-based tag index; ``len(tags)`` is
    Unreleased) and the count of finished items that could not be placed at all."""
    repo = q.repo
    assert repo is not None
    tasks = [i for i in q.tasks("done") if i.state == DONE]
    shas = {i.merged_sha for i in tasks if i.merged_sha}
    info = _known(repo, shas)
    second = {p[1] for _, p in info.values() if len(p) > 1}
    info.update(_known(repo, second - set(info)))
    _load_commits(repo, tags, shas | second)
    named: set[str] = set()  # bugs a LISTED task already speaks for

    rows: list[dict[str, Any]] = []
    unplaced = 0

    def place(item: Item | None, at: str) -> tuple[int | None, bool]:
        if item is not None and item.merged_sha in info:
            for n, t in enumerate(tags):
                if item.merged_sha in t.commits or _second(item, info) in t.commits:
                    if reached(repo, cfg, item, t.name):
                        return n, False
            return len(tags), False
        return _by_date(tags, at), True

    for it in tasks:
        field = it.changelog or {}
        if field.get("skip"):
            continue
        idx, fallback = place(it, it.completed_at)
        if idx is None:
            unplaced += 1
            continue
        category = field.get("category") or _category(it, cfg, info)
        line = field.get("line") or _first_sentence(_FIXES.sub("", it.title or "")) or it.id
        rows.append(_row(it.id, category, line, it.completed_at, idx, fallback))
        named.update(_fixed_bugs(it.title))

    for bug in q.bugs():
        field = bug.changelog or {}
        if field.get("skip") or not bug.fixed_at or not field.get("line") or bug.id in named:
            continue
        idx, fallback = place(None, bug.fixed_at)
        if idx is None:
            unplaced += 1
            continue
        rows.append(
            _row(bug.id, field.get("category", ""), field["line"], bug.fixed_at, idx, fallback)
        )
    return rows, unplaced


def _second(it: Item, info: dict[str, tuple[str, list[str]]]) -> str:
    """The merged branch tip of a merge-commit landing ('' when there is none): the commit
    ``reached()`` also accepts, used here only to skip tags that cannot contain it."""
    parents = info.get(it.merged_sha, ("", []))[1]
    return parents[1] if len(parents) > 1 else ""


def _category(it: Item, cfg: Any, info: dict[str, tuple[str, list[str]]]) -> str:
    subject, parents = info.get(it.merged_sha, ("", []))
    cat = _cc_category(subject)
    if not cat and len(parents) > 1:
        cat = _cc_category(info.get(parents[1], ("", []))[0])
    if cat:
        return cat
    bump = F.item_bump(it, cfg)
    if bump in _BUMP_CATEGORY:
        return _BUMP_CATEGORY[bump]
    return "Fixed" if _fixed_bugs(it.title) else "Changed"


def _row(ident: str, category: str, line: str, at: str, idx: int, fallback: bool) -> dict[str, Any]:
    return {
        "id": ident,
        "category": category if category in CHANGELOG_CATEGORIES else "Changed",
        "line": one_line(line, LINE_CHARS),
        "at": at,
        "section": idx,
        "by_date": fallback,
    }


def _section(title: str, version: str, date: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = []
    for cat in CHANGELOG_CATEGORIES:
        seen: set[str] = set()
        entries = []
        for r in sorted(
            (r for r in rows if r["category"] == cat), key=lambda r: (r["at"], r["id"])
        ):
            if r["line"] in seen:  # two tasks, one sentence: say it once
                continue
            seen.add(r["line"])
            entries.append({"id": r["id"], "line": r["line"], "by_date": r["by_date"]})
        if entries:
            groups.append({"category": cat, "entries": entries})
    return {
        "title": title,
        "version": version,
        "date": date,
        "unreleased": title == "Unreleased",
        "groups": groups,
        "by_date": any(e["by_date"] for g in groups for e in g["entries"]),
    }


def build_sections(q: Query) -> dict[str, Any]:
    """Every section (newest first), the baseline tag and the compare links."""
    if q.repo is None:
        raise ExportError("the changelog needs a repository: it reads git tags", EXIT_UNAVAILABLE)
    from ...config import Config

    try:
        cfg = Config.load(q.repo)
    except Exception as exc:
        raise ExportError(f"could not read the configuration: {type(exc).__name__}: {exc}") from exc
    prefix = cfg.flow.tag_prefix
    tags = _tags(q.repo, prefix)
    rows, unplaced = _entries(q, cfg, tags)
    sections = [_section("Unreleased", "", "", [r for r in rows if r["section"] == len(tags)])]
    for n in range(len(tags) - 1, 0, -1):  # index 0 is the baseline: nothing before it
        t = tags[n]
        sections.append(
            _section(t.version, t.version, t.date, [r for r in rows if r["section"] == n])
        )
    base = _remote_base(q.repo)
    links: list[dict[str, str]] = []
    if base and tags:
        links.append({"label": "Unreleased", "url": f"{base}/compare/{tags[-1].name}...HEAD"})
        for n in range(len(tags) - 1, 0, -1):
            links.append(
                {
                    "label": tags[n].version,
                    "url": f"{base}/compare/{tags[n - 1].name}...{tags[n].name}",
                }
            )
    return {
        "sections": sections,
        "links": links,
        "baseline": tags[0].name if tags else "",
        "tag_prefix": prefix,
        "unplaced": unplaced,
    }


def _pick(built: dict[str, Any], want: str, prefix: str) -> list[dict[str, Any]]:
    w = want.strip()
    if w.lower() == UNRELEASED:
        return [s for s in built["sections"] if s["unreleased"]]
    bare = w[len(prefix) :] if prefix and w.startswith(prefix) else w
    chosen = [s for s in built["sections"] if not s["unreleased"] and s["version"] == bare]
    if not chosen:
        known = ", ".join(s["version"] for s in built["sections"] if not s["unreleased"])
        raise ExportError(
            f"no changelog section for version {want!r} (the first tag is the baseline and has"
            f" none). Sections: {known or '(none)'}, {UNRELEASED}",
            registry.EXIT_REFUSED,
        )
    return chosen


def data(q: Query, f: registry.Filters) -> dict[str, Any]:
    built = build_sections(q)
    sections = built["sections"]
    single = bool(f.tag)
    if single:
        sections = _pick(built, f.tag, built["tag_prefix"])
    shown = {s["title"] for s in sections}
    return {
        "single": single,
        "sections": sections,
        "links": [] if single else built["links"],
        "baseline": built["baseline"],
        "unplaced": built["unplaced"],
        "has_by_date": any(s["by_date"] for s in sections),
        "shown": sorted(shown),
    }


def append_unreleased(q: Query):
    """Append-mode producer for ``write.append_entries``: ``produce(last) -> (text, new_last)``.

    The text is one ``- Category: line`` bullet per Unreleased entry whose finishing event
    (``item.completed`` / ``bug.fixed``) comes after the event id ``last`` recorded in the
    file's header; ``new_last`` is the log's newest event id. Old lines are never rewritten.
    """
    built = build_sections(q)
    unreleased = next(s for s in built["sections"] if s["unreleased"])
    order = {
        (e.kind, e.subject): n
        for n, e in enumerate(q.events)
        if e.kind in ("item.completed", "bug.fixed")
    }
    ids = [e.id or e.compute_id() for e in q.events]

    def produce(last: str) -> tuple[str, str]:
        floor = ids.index(last) if last in ids else -1
        lines = []
        for g in unreleased["groups"]:
            for e in g["entries"]:
                at = max(
                    (n for (k, s), n in order.items() if s == e["id"]),
                    default=-1,
                )
                if at > floor:
                    lines.append(f"- {g['category']}: {e['line']}\n")
        return "".join(lines), q.last_event_id

    return produce


registry.register(
    registry.DocKind(
        name="changelog",
        default_target="CHANGELOG.md",
        data=data,
        update_mode=registry.WHOLE,
        filters=frozenset({"tag"}),
        title="CHANGELOG.md in Keep a Changelog form, from items, bugs and version tags",
    )
)

__all__ = ["append_unreleased", "build_sections", "data"]
