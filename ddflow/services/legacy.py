"""Onboarding stages 4-5: cut the old workflow over, then freeze what was imported.

The import made the project's journals, lessons files and backlog into ddflow records --
but the RULEBOOK still tells every agent to keep writing them. A rulebook line that
survives the cutover is worse than an unused file: the agent follows the rule, edits a
file nothing reads, and the ddflow record silently disagrees. Stage 4 finds those lines
and proposes the ddflow replacement for each; the OPERATOR's text is not rewritten here,
only shown (onboard.md: "show the proposed rulebook diff before applying it").

Stage 5 makes the edit fail. Which files are frozen is NOT guessed from a rulebook: the
import already recorded its origin on every record it made -- `Item.source`,
`Memory.source`, `ResearchNote.sources` are `<file>:<line>` (R-onboard-legacy-imported-
sources) -- so `imported_files` derives the set from what actually happened. The ratchet
is a `pre-commit` `language: fail` hook where the project uses the framework
(R-onboard-legacy-precommit-fail), otherwise a generated test that pins each file's
sha256, so the `unit_tests` gate goes red instead of the record forking in silence.
`.ddflow/frozen.toml` is the manifest the generated test and `check_frozen` both read;
B-onboard-verify reports "unchanged since import" from it. The pre-commit hook embeds the
path list (pre-commit cannot read the manifest), and pre-commit never passes a DELETED
file to a hook, so deletion is caught by `check_frozen` at verification rather than at
the commit.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.digest import hasher
from ..infra.fsio import repo_rel
from ..infra.tomlcfg import atomic_write, basic_string
from .adopt import NATIVE_RULES, Refused, block_marker
from .enforce import UnreadableYaml, read_precommit_yaml

#: Where the onboarding prompt looks for instructions that write an imported surface.
#: The native list comes from `adopt.NATIVE_RULES` -- the same source `enforce.rulebooks`
#: derives from -- so an agent added there is not silently left unscanned (critic on
#: b71fc69). Its ddflow-managed blocks are skipped by `scan` with every other block.
RULEBOOK_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "CLAUDE.local.md",
    *(rule.path for rule in NATIVE_RULES.values()),
)
#: The harness's slash commands, which often carry the same duties in yet another copy.
COMMANDS_GLOB = ".claude/commands/*.md"

#: A duty a rulebook line asks for, and what ddflow does instead. First match wins, so
#: the more specific duties come first; a line matching none but naming an imported file
#: still gets a proposal (see `scan`). The last four move a convention into config, as
#: stage 4 asks, instead of leaving it as prose nothing enforces.
DUTY_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bco-?author(ed)?[- ]by\b|\battribution trailer\b", re.I),
        'make it config, not prose: `[enforce] forbidden_trailers = ["Co-Authored-By"]` '
        "-- the commit-msg hook refuses the trailer from every route",
    ),
    (
        re.compile(
            r"\bcommit trailer\b|\btrailer\b.{0,20}\b(convention|key|keys)\b",
            re.I,
        ),
        "make it config, not prose: `[enforce] item_trailer_keys` names the trailers ddflow reads",
    ),
    (
        re.compile(
            r"\b(weekly|daily|monthly|every \d+ days?)\b.{0,30}\b(bug ?hunt|dedupe|audit)\b"
            r"|\b(bug ?hunt|dedupe)\b.{0,30}\b(weekly|daily|monthly)\b",
            re.I,
        ),
        "make it a cadence, not a habit: `[cadence] every_days = N` -- ddflow surfaces it when due",
    ),
    (
        re.compile(r"\bsibling (repo|repository)\b|\bwait(s|ing)? (on|for) .{0,24}\brepo", re.I),
        "make it config, not prose: `[schedule] repos` -- an external dependency is observed, not remembered",
    ),
    (
        re.compile(
            r"\btick(ed|ing)?\b.{0,24}\b(box|checkbox)\b|\bcheck(ed|ing)? the (box|checkbox)\b",
            re.I,
        ),
        "claim the item and complete it through its gates -- `ddflow_complete` refuses a "
        "gate with no outcome, which is what the checkbox was for",
    ),
    (
        re.compile(r"\bSTATUS\b.{0,24}\bline\b", re.I),
        "the item's state and gate outcomes ARE the status -- `ddflow_brief` / `ddflow_status`",
    ),
    (
        re.compile(
            r"\b(append|add|write|update)\w*\b.{0,30}\b(journal|LOG\.md|log file|handoff)\b", re.I
        ),
        "record it with `ddflow_session_note` (the journal is a ddflow projection now)",
    ),
    (
        re.compile(r"\b(update|add to|append to|write to)\w*\b.{0,30}\blessons?\b", re.I),
        "record the transferable rule with `ddflow_lesson_add`, not an edit to lessons.md",
    ),
    (
        re.compile(r"\b(check|read|consult|review)\w*\b.{0,30}\blessons?\b", re.I),
        "`ddflow_recall` searches decisions, lessons, research and bugs in one call",
    ),
    (
        re.compile(r"\bresearch (log|file|doc)\b|\bresearch\.md\b", re.I),
        "record the verdict with `ddflow_research_add`; the citations document stays live",
    ),
    (
        re.compile(r"\b(create|make|use|open)\w*\b.{0,20}\bworktree\b", re.I),
        "`ddflow_claim` gives the worktree; land with `ddflow_merge`, finish with "
        "`ddflow_complete` -- the procedure is enforced, not prose",
    ),
)

#: The frozen set, committed: path -> sha256. One file the ratchet, the generated test
#: and `check_frozen` all read, so they cannot disagree about what is frozen.
MANIFEST_REL = ".ddflow/frozen.toml"
#: The generated test when the project does not use the pre-commit framework.
GENERATED_TEST = "tests/test_frozen_imports.py"
GENERATED_TEST_ROOT = "test_frozen_imports.py"
#: Ownership markers. A file carrying the first is ddflow's to regenerate; one without
#: it is the operator's and is never touched.
TEST_MARK = "# ddflow: frozen imported files"
HOOK_BEGIN = "# ddflow: frozen imported files (managed by onboard stage 5; regenerated)"
HOOK_END = "# ddflow: end frozen imported files"
HOOK_ID = "ddflow-frozen-imports"

_CHUNK = 1 << 20


@dataclass(frozen=True)
class Proposal:
    """One rulebook line that still writes an imported surface, and its replacement."""

    path: str
    line: int
    text: str
    replacement: str


#: `<path>:<line>`, where the path looks like one (a dot or a slash in it). A bare word
#: before `:123` is an identifier (`issue:123`), not a file, and freezing "issue" would
#: report a phantom.
_FILE_ORIGIN = re.compile(r"^(?=.+[./])(.+):\d+$")


def files_from_sources(sources: Iterable[str]) -> list[str]:
    """The file part of `<file>:<line>` origins, deduplicated and sorted.

    Only a `<path>:<line>` origin names a file (importer.py writes exactly that, and
    `git:feature/x` is a branch). `issue:123`, a URL, or prose an import answered
    without a source is dropped rather than turned into a file that `freeze` would then
    report as missing (roborev on c8fbfee).
    """
    out: set[str] = set()
    for source in sources:
        match = _FILE_ORIGIN.match((source or "").strip())
        if not match:
            continue
        path = match.group(1)
        if "://" in path:
            continue
        out.add(path)
    return sorted(out)


def imported_files(state: Any) -> list[str]:
    """The files the import actually consumed, from the records' own origins.

    Read from the projection, not from the configured globs: a glob matches files that
    yielded nothing and files that are templates, and freezing those would be freezing
    the wrong thing (D-readme-current's "counts must stay true" one layer down).
    """
    items = (getattr(r, "source", "") for r in getattr(state, "items", {}).values())
    memories = (getattr(r, "source", "") for r in getattr(state, "memories", {}).values())
    research = (
        s
        for r in getattr(state, "research", {}).values()
        for s in (getattr(r, "sources", None) or [])
    )
    return files_from_sources([*items, *memories, *research])


def scan(repo: Path, imported: Iterable[str], *, extra: Iterable[str] = ()) -> list[Proposal]:
    """Rulebook lines that still tell agents to write an imported surface.

    Files scanned: the native rulebooks, the harness's slash commands, and any handoff
    document the operator names (`extra`) -- handoff docs have no discoverable location.
    ddflow's own managed block is skipped: it is regenerated, and its text already speaks
    ddflow. A line that already mentions ddflow is left alone, whatever it says.
    """
    repo = Path(repo)
    imported = list(imported)
    extra = list(extra)
    missing_named = [
        name
        for name in extra
        if not (Path(name) if Path(name).is_absolute() else repo / name).is_file()
    ]
    if missing_named:
        # Skipping a file the OPERATOR named would make render's "every file the operator
        # named" claim false and turn "nobody looked" into "nothing found" (roborev on
        # c8fbfee, the vacuous-pass class this project keeps catching).
        raise ValueError(
            f"named file(s) do not exist, so they were not scanned: {', '.join(missing_named)}"
        )
    seen: set[Path] = set()
    candidates = [repo / rel for rel in RULEBOOK_FILES]
    candidates += sorted(repo.glob(COMMANDS_GLOB))
    candidates += [Path(e) if Path(e).is_absolute() else repo / e for e in extra]
    out: list[Proposal] = []
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        # a Path, so the label keeps native separators, as it always has
        rel = Path(repo_rel(repo, path, as_given=True, strict=False) or path)
        text = path.read_text("utf-8", errors="replace")
        inside = False
        for number, line in enumerate(text.splitlines(), 1):
            marker = block_marker(line)
            if marker:
                inside = marker == "begin"
                continue
            if inside:
                continue
            replacement = _replacement_for(line, imported)
            if replacement:
                out.append(Proposal(str(rel), number, line.strip(), replacement))
    return out


def _replacement_for(line: str, imported: list[str]) -> str:
    """What the line should say instead, or "" when it is not a cutover candidate."""
    text = line.strip()
    if not text or "ddflow" in text.lower():
        return ""
    for duty, replacement in DUTY_REPLACEMENTS:
        if duty.search(text):
            return replacement
    for rel in imported:
        if _mentions(text, rel):
            return (
                f"`{rel}` was imported into ddflow and is frozen; the queue is the source "
                f"of truth -- `ddflow_next`, and record with `ddflow_session_note` "
                f"instead of editing it"
            )
    return ""


def _mentions(text: str, rel: str) -> bool:
    """Does `text` name `rel` (or its basename) with a real boundary?

    A bare substring match makes `LOG.md` match the middle of `BACKLOG.md`, and a
    proposal about the wrong file is worse than none.
    """
    for needle in (rel, Path(rel).name):
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])", text):
            return True
    return False


def render(proposals: Iterable[Proposal]) -> str:
    """The report the operator reads and approves, one proposal per line pair."""
    items = list(proposals)
    if not items:
        return (
            "no cutover proposal in the files scanned (the native rulebooks, "
            ".claude/commands/*.md, and every file the operator named); name any other "
            "prompt or rulebook file explicitly so it is scanned too"
        )
    return "\n".join(f"{p.path}:{p.line}: {p.text}\n  -> {p.replacement}" for p in items)


def sha256_file(path: Path) -> str:
    """The hash of `path`'s bytes, streamed so a large file is not read into memory."""
    digest = hasher()
    with Path(path).open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(repo: Path, paths: Iterable[str]) -> str:
    """Write `.ddflow/frozen.toml` for `paths` (existing files only) and say so."""
    repo = Path(repo)
    frozen = {rel: sha256_file(repo / rel) for rel in paths if (repo / rel).is_file()}
    body = [
        "# Frozen by the ddflow onboarding (stage 5). These files were imported into ddflow:",
        "# editing one reaches no agent and forks the record. Record with",
        "# `ddflow_lesson_add` / `ddflow_session_note` / `ddflow_research_add`, or ask the",
        "# operator to unfreeze (remove it here and update the ratchet).",
        "[frozen]",
    ]
    # basic_string, not json.dumps: an emoji in a path became a surrogate pair (Bb11e7a8186)
    body += [
        f"{basic_string(rel)} = {basic_string(digest)}" for rel, digest in sorted(frozen.items())
    ]
    atomic_write(repo / MANIFEST_REL, "\n".join(body) + "\n")
    return f"wrote {MANIFEST_REL} with {len(frozen)} frozen file(s)"


def read_frozen(repo: Path) -> dict[str, str] | None:
    """The frozen set; None when NO manifest exists.

    `{}` is a manifest that validly freezes nothing (the operator unfroze the last file,
    as the manifest's own comment says they may), and is deliberately distinct from "the
    ratchet was never generated" -- the collapse that would report a missing ratchet as
    an empty, passing one. Raises ValueError for a manifest that exists but is not a
    `[frozen]` table of strings: "could not read" is not "nothing is frozen" either.
    """
    path = Path(repo) / MANIFEST_REL
    if not path.is_file():
        return None
    data = tomllib.loads(path.read_text("utf-8"))
    table = data.get("frozen")
    if not isinstance(table, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in table.items()
    ):
        raise ValueError(f"{MANIFEST_REL}: [frozen] is not a table of path = sha256 strings")
    return table


def check_frozen(repo: Path) -> list[str] | None:
    """Frozen paths whose bytes changed or that are gone; `[]` when all match.

    None means there is NO manifest -- the ratchet was never generated -- which is NOT
    "everything is unchanged"; a present-but-empty manifest is `[]`, with nothing to
    drift.
    """
    repo = Path(repo)
    frozen = read_frozen(repo)
    if frozen is None:
        return None
    drifted = []
    for rel, want in sorted(frozen.items()):
        path = repo / rel
        got = sha256_file(path) if path.is_file() else "missing"
        if got != want:
            drifted.append(rel)
    return drifted


def freeze(
    repo: Path, paths: Iterable[str], *, config_rel: str = ".pre-commit-config.yaml"
) -> list[str]:
    """Write the frozen manifest and arm ONE ratchet for it, and say what was done.

    The pre-commit framework wins where it is used: a `language: fail` hook refuses the
    commit and prints what to use instead -- it catches EDITS, since pre-commit passes
    only existing files; a deletion is caught by `check_frozen`. Elsewhere a generated
    test pins the hashes, so the project's own `unit_tests` gate goes red. Files that no
    longer exist are reported, not invented into the manifest.
    """
    repo = Path(repo)
    paths = list(paths)
    existing = [rel for rel in paths if (repo / rel).is_file()]
    out: list[str] = []
    missing = [rel for rel in paths if rel not in existing]
    if missing:
        out.append(f"not frozen (no longer exists): {', '.join(missing)}")
    if not existing:
        out.append("no imported files to freeze; no ratchet written")
        return out
    # Arm the ratchet BEFORE the manifest: a refusal from here then leaves nothing
    # behind, and the generated test reads the manifest only when the suite runs.
    config = repo / config_rel
    if config.is_file():
        armed = _arm_precommit(repo, config, existing)
    else:
        armed = _generate_test(repo, existing)
    if isinstance(armed, Refused):
        return [*out, armed]
    out.append(armed)
    out.append(write_manifest(repo, existing))
    return out


def _arm_precommit(repo: Path, config: Path, paths: list[str]) -> str:
    """Add or replace the managed `language: fail` hook block in the pre-commit config.

    The config is edited as TEXT and the result is fed back through `enforce`'s YAML
    reader before it is written -- PyYAML is deliberately not a dependency, and a
    hand-built block that does not parse must be a refusal with the original left alone.
    """
    text = config.read_text("utf-8")
    indent = _repos_item_indent(text)
    block = _hook_block(paths, indent)
    begins, ends = text.count(HOOK_BEGIN), text.count(HOOK_END)
    if begins != ends or begins > 1:
        # Replacing a half-deleted block by finding the first END would swallow whatever
        # sits between the orphan marker and the next block (rubber_duck on 276c2cbe).
        return Refused(
            f"{config.name} has {begins} begin and {ends} end marker(s) for ddflow's "
            f"frozen-files hook, expected one of each; clean the block up by hand and "
            f"re-run"
        )
    if begins == 1:
        start = text.rfind("\n", 0, text.index(HOOK_BEGIN)) + 1
        end_at = text.find(HOOK_END, start)
        if end_at == -1:
            return Refused(
                f"{config.name}'s hook end marker comes before its begin marker; fix the "
                f"block by hand and re-run"
            )
        newline = text.find("\n", end_at)
        stop = len(text) if newline == -1 else newline + 1
        new_text = text[:start] + block + text[stop:]
        action = f"updated the frozen-files hook in {config.name}" + _INSTALL_NOTE
    else:
        new_text = _insert_hook(text, block)
        action = f"added the frozen-files hook to {config.name}" + _INSTALL_NOTE
    try:
        read_precommit_yaml(new_text)
    except UnreadableYaml as exc:
        return Refused(
            f"{config.name} would not parse after the edit ({exc}); add a "
            f"`language: fail` hook for {', '.join(paths)} by hand"
        )
    atomic_write(config, new_text)
    return action + _superseded_note(repo, _remove_marked_tests(repo))


def _repos_item_indent(text: str) -> str:
    """The indentation existing `repos:` items use; two spaces when there are none.

    Sequence items on one level must align, so the block has to match the file, not the
    style this module would have chosen. An item may legitimately start at column 0,
    which is also what `""` returns here -- the caller uses it as-is.
    """
    match = re.search(r"^repos:[ \t]*(?:#.*)?$", text, re.M)
    if not match:
        return "  "
    for line in text[match.end() :].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-"):
            return line[: len(line) - len(line.lstrip())]
        if not line[0].isspace():
            break
    return "  "


def _hook_block(paths: list[str], indent: str) -> str:
    """The managed hook, at `indent` so its items align with the config's own."""
    pattern = "^(?:" + "|".join(re.escape(rel) for rel in paths) + ")$"
    quoted = "'" + pattern.replace("'", "''") + "'"
    pad = indent
    return "\n".join(
        [
            f"{pad}{HOOK_BEGIN}",
            f"{pad}- repo: local",
            f"{pad}  hooks:",
            f"{pad}    - id: {HOOK_ID}",
            f"{pad}      name: imported files are frozen -- record with a ddflow tool, do not edit",
            f"{pad}      entry: imported into ddflow; use ddflow_lesson_add, ddflow_session_note or ddflow_research_add instead",
            f"{pad}      language: fail",
            f"{pad}      files: {quoted}",
            f"{pad}{HOOK_END}",
            "",
        ]
    )


def _insert_hook(text: str, block: str) -> str:
    """`text` with `block` under its `repos:` key, creating the key when absent."""
    match = re.search(r"^repos:([ \t]*)(\S.*)?$", text, re.M)
    if match:
        tail = match.end()
        if text[tail : tail + 1] == "\n":
            tail += 1
        if match.group(2):
            # `repos: []` (or any inline value): replace the line, keeping both.
            return text[: match.start()] + "repos:\n" + block + text[tail:]
        return text[: match.end()] + "\n" + block + text[tail:]
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "repos:\n" + block


#: The hook only fires when the framework is installed; said on every arm action, so the
#: operator is not promised a refusal that never happens (critic on b71fc69).
_INSTALL_NOTE = " (it fires once the pre-commit framework is installed: `pre-commit install`)"


def _generated_test_candidates(repo: Path) -> list[Path]:
    """Both places the generated test may live; which one is used depends on tests/."""
    return [repo / GENERATED_TEST, repo / GENERATED_TEST_ROOT]


def _remove_marked_tests(repo: Path, *, keep: Path | None = None) -> list[Path]:
    """Drop ddflow's OWN generated tests at the other location(s), report them.

    A project that grows a `tests/` directory or adopts pre-commit after a first freeze
    would otherwise keep a stale second ratchet: the docstring promises ONE, and two
    copies of the same check are one place for the two to drift (roborev on c8fbfee).
    A file without the marker is the operator's and is never touched.
    """
    dropped: list[Path] = []
    for path in _generated_test_candidates(repo):
        if path == keep or not path.is_file():
            continue
        if TEST_MARK in path.read_text("utf-8", errors="replace"):
            path.unlink()
            dropped.append(path)
    return dropped


def _superseded_note(repo: Path, dropped: list[Path]) -> str:
    if not dropped:
        return ""
    return "; removed the superseded ratchet at " + ", ".join(
        str(path.relative_to(repo)) for path in dropped
    )


def _generate_test(repo: Path, paths: list[str]) -> str:
    """Write the hash-pinning test; the project has no pre-commit framework."""
    target = repo / GENERATED_TEST
    if not (repo / "tests").is_dir():
        target = repo / GENERATED_TEST_ROOT
    if target.exists() and TEST_MARK not in target.read_text("utf-8", errors="replace"):
        return Refused(
            f"{target.relative_to(repo)} is already there and is not ddflow's; not "
            f"touching it -- add the hash check by hand"
        )
    depth = 1 if target.parent.name == "tests" else 0
    body = (
        f'"""{TEST_MARK}.\\n\\n'
        f"These files were imported into ddflow: editing one reaches no agent and forks\\n"
        f"the record. Record with ddflow_lesson_add / ddflow_session_note /\\n"
        f"ddflow_research_add, or ask the operator to unfreeze. Generated by\\n"
        f"the ddflow onboarding; the hashes live in {MANIFEST_REL}.\\n"
        f'"""\n\n'
        f"import tomllib\n"
        f"from hashlib import sha256\n"
        f"from pathlib import Path\n\n"
        f"ROOT = Path(__file__).resolve().parents[{depth}]\n\n\n"
        f"def test_frozen_imports_are_unchanged():\n"
        f'    frozen = tomllib.loads((ROOT / "{MANIFEST_REL}").read_text("utf-8"))["frozen"]\n'
        f"    drifted = []\n"
        f"    for rel, want in sorted(frozen.items()):\n"
        f"        path = ROOT / rel\n"
        f'        got = sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"\n'
        f"        if got != want:\n"
        f"            drifted.append(rel)\n"
        f"    assert not drifted, (\n"
        f'        "these files were imported into ddflow and are frozen; editing one reaches "\n'
        f'        "no agent. Use ddflow_lesson_add / ddflow_session_note / ddflow_research_add, "\n'
        f'        "or ask the operator to unfreeze: " + ", ".join(drifted)\n'
        f"    )\n"
    )
    atomic_write(target, body)
    note = _superseded_note(repo, _remove_marked_tests(repo, keep=target))
    return (
        f"wrote {target.relative_to(repo)}: change a byte and watch it fail, then restore; "
        f"it turns the suite red once the project's unit_tests command collects it{note}"
    )
