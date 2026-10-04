"""Onboarding imports the agent harness's own project memory as ddflow memories.

Claude Code keeps a project's own memory outside the repository, under
`~/.claude/projects/<slug>/memory/`: one `MEMORY.md` index and one frontmatter `.md` file
per fact (`R-onboard-harness-memory-layout`). The onboard prompt's cutover stage ends
with "offer to record each still-true one with `ddflow_memory_add`" -- this module is the
service half of that offer. It finds the right directory (the slug encodes the checkout
path), reads the facts AGAINST the index (a fact missing from the index, or a link with
no file, is reported -- `L-import-read-against-handoff`), and splits what is new from
what is already remembered using the same engine and thresholds an add uses
(`R-onboard-harness-memory-dedupe`).

Nothing is written until `apply`, and `apply` records only the records the operator
approved: the harness's memory is the operator's text, and an import that records all of
it unasked is a queue nobody trusts. Ids are derived from the file name, not from
`auto_id` -- the latter deliberately salts the clock, so the same fact would arrive under
a new id on every run (`R-onboard-harness-memory-dedupe`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..infra.log import EventLog
from .enforce import UnreadableYaml, read_precommit_yaml
from .importer import Duplicate, Found

#: Where Claude Code roots its per-project state, and the name of the memory directory.
MEMORY_DIRNAME = "memory"
INDEX_NAME = "MEMORY.md"
#: The tags an imported memory carries: the OptMem import's own tag, plus its origin, so
#: `recall` can tell harness memories from ones an agent recorded here.
IMPORTED_TAGS = ["imported", "claude-memory"]

_LINK = re.compile(r"\]\(([^)#]+\.md)\)")


@dataclass
class HarnessScan:
    """What a checkout's harness memory holds, and where it disagrees with itself."""

    directory: Path | None = None
    found: list[Found] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def project_slug(path: Path) -> str:
    """Claude Code's directory name for a checkout: every non-alphanumeric byte -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def harness_memory_dir(repo: Path, *, projects_root: Path | None = None) -> Path | None:
    """The memory directory the harness keeps for `repo`, or None when it has none."""
    root = (
        Path(projects_root) if projects_root is not None else Path.home() / ".claude" / "projects"
    )
    candidate = root / project_slug(repo) / MEMORY_DIRNAME
    return candidate if candidate.is_dir() else None


def _slug(text: str, limit: int) -> str:
    """A stable, readable id fragment; `""` only for text with no alphanumerics at all."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit]


def _frontmatter(text: str) -> tuple[dict, str, str]:
    """(frontmatter, body, problem) of one memory file.

    Read with `enforce.read_precommit_yaml` -- the subset this project already reads
    without making PyYAML a dependency. A file outside that subset is a PROBLEM the
    operator sees, never a fact silently dropped.
    """
    if not text.startswith("---"):
        return {}, text, ""
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            raw = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :])
            try:
                fm = read_precommit_yaml(raw)
            except UnreadableYaml as exc:
                return {}, body, f"frontmatter could not be read ({exc})"
            if not isinstance(fm, dict):
                return {}, body, "frontmatter is not a mapping"
            return fm, body, ""
    return {}, text, "frontmatter has no closing '---'"


def _first_paragraph(body: str) -> str:
    for block in re.split(r"\n\s*\n", body):
        line = " ".join(block.split())
        if line:
            return line
    return ""


def _note(path: Path) -> tuple[Found | None, str]:
    """(record, problem) for one memory file. The description IS the memory: it is the
    one-line fact the harness itself surfaces, where the body carries the reasoning that
    belongs in a lesson, not in something `brief` shows every session."""
    text = path.read_text("utf-8", errors="replace")
    fm, body, problem = _frontmatter(text)
    if problem:
        return None, f"{path.name}: {problem}"
    description = fm.get("description")
    memory = description.strip().strip("\"'") if isinstance(description, str) else ""
    memory = memory or _first_paragraph(body)
    if not memory:
        return None, f"{path.name}: no description and no body"
    metadata = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    name = fm.get("name") if isinstance(fm.get("name"), str) and fm.get("name") else path.stem
    modified = metadata.get("modified", "")
    ident = f"M-harness-{_slug(path.stem, 28)}"
    if ident == "M-harness-":
        ident = f"M-harness-{_slug(name, 28)}"
    if ident == "M-harness-":
        return None, f"{path.name}: the file name yields no id"
    return (
        Found(
            kind="memory",
            ident=ident,
            title=name,
            source=f"claude-memory/{path.name}",
            body=memory,
            extra={
                "origin_at": modified if isinstance(modified, str) else "",
                "file": str(path),
            },
        ),
        "",
    )


def scan(repo: Path, *, projects_root: Path | None = None) -> HarnessScan:
    """The checkout's harness memory, read against its own index.

    `MEMORY.md` is an index, not a fact: its one-liners repeat the files it links, so it
    is read to CHECK the facts (a file it does not list, a link with no file), never
    imported itself.
    """
    directory = harness_memory_dir(repo, projects_root=projects_root)
    if directory is None:
        return HarnessScan()
    out = HarnessScan(directory=directory)
    index = directory / INDEX_NAME
    listed: set[str] = set()
    idents: dict[str, str] = {}
    if index.is_file():
        listed = set(_LINK.findall(index.read_text("utf-8", errors="replace")))
        for name in sorted(listed):
            if not (directory / name).is_file():
                out.problems.append(f"{INDEX_NAME} links {name}, which does not exist")
    for path in sorted(directory.glob("*.md")):
        if path.name == INDEX_NAME:
            continue
        found, problem = _note(path)
        if problem:
            out.problems.append(problem)
            continue
        if found is None:
            continue
        out.found.append(found)
        clash = idents.get(found.ident)
        if clash is not None:
            # `a_b.md` and `a-b.md` slug to one id, and the fold keeps one text per id:
            # the second fact would vanish at apply time (rubber_duck on a86e6f43).
            out.problems.append(
                f"{path.name} and {clash} yield the same id {found.ident}; rename one"
            )
        else:
            idents[found.ident] = path.name
        if index.is_file() and path.name not in listed:
            out.problems.append(f"{path.name} is not listed in {INDEX_NAME}")
    return out


def _as_record(f: Found) -> dict[str, str]:
    """A memory as the similarity engine reads it: `infra.store.similar_records` indexes a
    memory with an EMPTY title and its text as the body, and comparing a different shape
    against that never matches identically."""
    return {"id": f.ident, "kind": f.kind, "title": "", "body": f.body, "item": ""}


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def dedupe(found: list[Found], state: Any, cfg: Config) -> tuple[list[Found], list[Duplicate]]:
    """Split `found` into the new facts and the ones already remembered.

    The engine and thresholds are the ones an add uses (`services.similar` over
    `similar_records`, `[dedupe]`), assessed against the QUEUE only: two facts of one
    import are kept even when they are close, because which of a pair to keep is the
    author's call (the importer's own rule) -- except an exact normalized copy, which is
    folded here so the operator sees one line instead of two.

    The check honours `[dedupe]` (kinds, `on_match`, thresholds) the way an add does:
    with `on_match = "off"` nothing is dropped, because the operator said not to check.
    """
    from ..infra.store import similar_records
    from . import similar

    base = similar_records(state) if state is not None else []
    index = similar.build(base)
    kept: list[Found] = []
    duplicates: list[Duplicate] = []
    seen: dict[str, Found] = {}
    for f in found:
        assessment = similar.assess(index, _as_record(f), cfg)
        hit = next(
            (
                c
                for c in assessment.candidates
                if "identical" in c.flags
                or (
                    c.score >= cfg.dedupe.ask_threshold and assessment.words >= cfg.dedupe.min_words
                )
            ),
            None,
        )
        if hit is not None:
            duplicates.append(Duplicate(f, hit.id, hit.score, "identical" in hit.flags, "queue"))
            continue
        key = _normalize(f.body)
        if key in seen:
            duplicates.append(Duplicate(f, seen[key].ident, 1.0, True, "import"))
            continue
        seen[key] = f
        kept.append(f)
    return kept, duplicates


def render(scan_result: HarnessScan, kept: list[Found], duplicates: list[Duplicate]) -> str:
    """The offer the operator reads: what would be remembered, what repeats, and what is
    wrong with the store itself."""
    if scan_result.directory is None:
        return "no harness memory directory for this checkout; nothing to import"
    lines = [f"harness memory: {scan_result.directory}"]
    for f in kept:
        lines.append(f"  remember {f.ident} [{f.title}]: {f.body}")
        lines.append(f"      from {f.source}")
    for d in duplicates:
        why = "identical to" if d.identical else f"reads like ({d.score:.2f})"
        lines.append(f"  skip {d.found.ident} [{d.found.title}]: {why} {d.of}")
    for problem in scan_result.problems:
        lines.append(f"  problem: {problem}")
    if not kept and not duplicates:
        lines.append("  no facts found")
    return "\n".join(lines)


def apply(log: EventLog, found: list[Found], *, state: Any = None) -> list[str]:
    """Record the approved facts as `memory.recorded`, once each, and say what happened.

    Idempotent by the deterministic id and by exact text: re-running onboarding must not
    double the queue. `state` is optional -- without one this folds the log itself,
    because a caller who forgets it must not silently double the queue (rubber_duck on
    a86e6f43). A fact over `[memory] max_chars` is REFUSED with what to do instead,
    never truncated -- the rule `memory_add` enforces; the importer's 4000-byte cap
    belongs to its own free-form store, not to a fresh import.

    Two facts whose file names slug to the same id are a REFUSAL, not a merge: the fold
    keeps one text per id, so appending both would silently lose one while the report
    called both remembered. And a fact that was FORGOTTEN is refused too: re-recording
    clears the reason, and the operator approving a plain "remember" line does not know
    they are reviving it.
    """
    from ..core.model import fold

    cfg = Config.load(log.root)
    if state is None:
        state = fold(log.read_all(), strict=False)
    memories = getattr(state, "memories", {})
    id_text = {mid: _normalize(m.text) for mid, m in memories.items() if getattr(m, "live", True)}
    text_forgotten = {
        _normalize(m.text): m for m in memories.values() if not getattr(m, "live", True)
    }
    texts = set(id_text.values())
    out: list[str] = []
    with log.transaction():
        for f in found:
            limit = cfg.memory.max_chars
            if len(f.body) > limit:
                out.append(
                    f"not recorded {f.ident}: {len(f.body)} characters is over "
                    f"[memory] max_chars ({limit}); write it as a lesson or shorten it "
                    f"({f.source})"
                )
                continue
            key = _normalize(f.body)
            if f.ident in id_text:
                if id_text[f.ident] == key:
                    out.append(f"already remembered: {f.ident} [{f.title}]")
                else:
                    out.append(
                        f"not recorded {f.ident}: another fact already uses this id with "
                        f"different text; rename {f.source} or record it by hand"
                    )
                continue
            if key in text_forgotten:
                out.append(
                    f"not recorded {f.ident}: this fact was forgotten "
                    f"({text_forgotten[key].forgotten}); re-record it deliberately with "
                    f"ddflow memory add if it is true again"
                )
                continue
            if key in texts:
                out.append(f"already remembered: {f.ident} [{f.title}]")
                continue
            log.append(
                "memory.recorded",
                f.ident,
                {
                    "text": f.body,
                    "origin_at": f.extra.get("origin_at", ""),
                    "source": f.source,
                    "tags": list(IMPORTED_TAGS),
                },
            )
            id_text[f.ident] = key
            texts.add(key)
            out.append(f"remembered {f.ident}: {f.body}")
    return out
