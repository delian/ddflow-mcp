"""`ddflow import --verify` on an imported record whose "source" is a sentence.

Bug Bd5b59f3894. A lesson corpus's `**Seen in:**` paragraph is imported verbatim into
`Lesson.seen_in`, and on a real corpus that paragraph is often prose that merely
MENTIONS a path ("... `config/training/initial_finetune.py` cited ~2839 ..."). The
vanished-source check accepted anything containing a `/` as a path and stat-ed it; a
sentence longer than a file name made `Path.is_file()` raise ENAMETOOLONG, and verify
crashed with a traceback instead of producing its report -- so on that project it never
ran at all. A shorter sentence did not crash; it was reported as a vanished file, a
false finding in the check whose value is that its findings are real.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK = 0

#: The shape of the record that crashed the real run: a dated sentence, over 255
#: characters before its first `/`, that cites paths in backticks.
LONG_PROSE = (
    "2026-08-21 Phase 136, roborev 414-D4 (and its earlier duplicate filing under job "
    "123). Fixing the duplication surfaced the stale reference; the repo-wide sweep then "
    "found two more, both fixed (`training_initial_finetune.py` cited ~2839 for a call "
    "at 1088; the stale citation in `config/training/initial_finetune.py` cited ~12)."
)
SHORT_PROSE = "Phase 12 review, see config/billing/tax.py for the call site"
#: One token, path-shaped, whose first component no filesystem accepts.
UNSTATABLE = "a" * 280 + "/b.md"


def _corpus(repo: Path, seen_in: list[str]) -> None:
    body = "# Lessons\n\n" + "".join(
        f"## Rule number {i}\nDo the thing {i}.\n\n**Seen in:** {s}\n\n"
        for i, s in enumerate(seen_in)
    )
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "lessons.md").write_text(body)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "history"], check=True, capture_output=True
    )
    run_cli(repo, "init")
    assert run_cli(repo, "import", "--apply")[0] == OK


def _verify(repo: Path) -> dict:
    code, out, err = run_cli(repo, "--json", "import", "--verify")
    assert out, f"verify produced no report (exit {code}):\n{err[-2000:]}"
    return json.loads(out)


def _vanished(data: dict) -> set[str]:
    return {v["source"] for v in data["vanished_sources"]}


def test_a_prose_seen_in_longer_than_a_file_name_does_not_crash_verify(repo):
    assert len(LONG_PROSE.split("/", 1)[0].encode()) > 255, "fixture no longer too long"
    _corpus(repo, [LONG_PROSE])
    data = _verify(repo)
    assert data["imported"].get("lesson") == 1, data["imported"]
    assert not any("Phase 136" in s for s in _vanished(data)), _vanished(data)


def test_a_short_prose_seen_in_that_mentions_a_path_is_not_a_vanished_file(repo):
    _corpus(repo, [SHORT_PROSE])
    data = _verify(repo)
    assert not any("Phase 12" in s for s in _vanished(data)), _vanished(data)
    # ...and the report says it did not look, rather than reading as checked-and-present.
    assert any("prose that mentions a path" in n for n in data["notes"]), data["notes"]


def test_a_path_shaped_source_that_cannot_be_stat_ed_is_neither_a_crash_nor_vanished(repo):
    _corpus(repo, [UNSTATABLE])
    data = _verify(repo)
    assert UNSTATABLE not in _vanished(data), _vanished(data)
    assert any("could not be checked" in n for n in data["notes"]), data["notes"]


def test_a_source_the_filesystem_refuses_is_never_reported_vanished(repo):
    """roborev 847: `Path.is_file()` answers False, not an exception, for a name the
    filesystem refuses outright -- on 3.14 for ENAMETOOLONG too, on every version for
    an embedded NUL. "Could not tell" must not be reported as "gone"."""
    refused = "docs/a\x00b.md"
    _corpus(repo, [refused])
    data = _verify(repo)
    assert refused not in _vanished(data), _vanished(data)
    assert any("could not be checked" in n for n in data["notes"]), data["notes"]


def test_a_path_in_markdown_backticks_is_checked_as_the_path_it_names(repo):
    """The run that surfaced the crash, once past it, reported 8 vanished sources and
    every one existed: `**Seen in:** `nemorun/cli/export.py:88`.` was stat-ed with its
    backtick on."""
    _corpus(repo, ["`docs/lessons.md:3`.", "`docs/gone.md`"])
    gone = _vanished(_verify(repo))
    assert not any("lessons.md" in s for s in gone), gone
    assert "docs/gone.md" in gone, gone


def test_a_real_missing_path_is_still_vanished_and_a_bug_pin_still_is_not(repo):
    _corpus(repo, [LONG_PROSE, "docs/gone.md", "review-inverted-severity"])
    data = _verify(repo)
    gone = _vanished(data)
    assert "docs/gone.md" in gone, gone
    assert "review-inverted-severity" not in gone, gone
    assert "docs/lessons.md" not in gone, gone


def test_a_markdown_link_is_checked_as_its_target(repo):
    """Rubber-duck finding: `[docs/a.md](docs/a.md)` lost only its outer bracket and
    paren to the unwrap and was stat-ed as `docs/a.md](docs/a.md`, so an existing file
    was reported vanished."""
    _corpus(
        repo,
        ["[docs/lessons.md](docs/lessons.md)", "[the gone file](docs/gone.md).", "`a.md`,`b.md`"],
    )
    gone = _vanished(_verify(repo))
    assert not any("lessons.md" in s for s in gone), gone
    assert "docs/gone.md" in gone, gone
    assert not any("a.md" in s for s in gone), gone


def test_a_titled_or_wrapped_markdown_link_is_checked_as_its_target(repo):
    """Critic finding: a link with a title, or inside backticks or bold, did not match
    the link pattern and was then rejected as markup, so a vanished target went
    unreported."""
    _corpus(
        repo,
        [
            '[gone one](docs/gone1.md "the title")',
            "`[gone two](docs/gone2.md)`",
            "**[gone three](docs/gone3.md)**.",
            "[gone four](docs/gone4.md 'single-quoted title')",
            "[gone five](docs/gone5.md (parenthesised title))",
            "__[gone six](docs/gone6.md)__",
            "**[gone seven](docs/gone7.md).**",
            "[gone eight](docs/gone(8).md)",
            '[here](docs/lessons.md "exists")',
            "[a heading](docs/lessons.md#rule-number-0)",
        ],
    )
    gone = _vanished(_verify(repo))
    assert {f"docs/gone{i}.md" for i in range(1, 8)} | {"docs/gone(8).md"} <= gone, gone
    assert not any("lessons.md" in s for s in gone), gone
