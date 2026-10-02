"""Documentation check core (B-docscheck-core): identifiers, flags, commands, links and
anchors a document names against the tree.

The failing-first fixture is this repository's README as of commit 7a397be, the day
before its catch-up: three lines of it, verbatim, whose drift the check must find.
"""

import subprocess
import time
from pathlib import Path

import pytest

from ddflow.services import docscheck as D

# README.md@7a397be, verbatim (table row 172, the TOC entry 207, lines 2627-2628). The
# heading the TOC entry names sat INSIDE a ```console sample there, so on GitHub it was
# never a heading: a broken anchor.
OLD_README = """\
# ddflow

- [The rules, and where each came from](#the-rules-and-where-each-came-from)
- [Reviewers](#reviewers)

## Reviewers

| **find out if we're going in circles** | `ddflow loops` | `ddflow_loops` |
| **record a lesson / decision / research / bug** | `ddflow lesson add` · `decision add` · `research` · `bug found\\|fixed` | `ddflow_lesson_add` · `ddflow_decision_add` · `ddflow_research` · `ddflow_bug_*` |

```console
$ ddflow workflow
# The workflow this project runs

## The rules, and where each came from

  gates.require_outcome                  True              [default]
```

- the **AGENTS.md / CLAUDE.md sections** `ddflow setup` writes, plus each agent's native
  rules file — the client re-reads its own rules, so this is the durable channel;
"""

FIXED_README = (
    OLD_README.replace("`ddflow_research`", "`ddflow_research_add`")
    .replace("`ddflow setup` writes", "`ddflow_setup` writes")
    .replace("## Reviewers\n\n|", "## Reviewers\n\n## The rules, and where each came from\n\n|")
    .replace("\n## The rules, and where each came from\n\n  gates", "\n  gates")
)

CODE = """\
import argparse

def build():
    s = argparse.ArgumentParser().add_subparsers()
    for name in ("loops", "workflow", "lesson", "decision", "research", "bug"):
        s.add_parser(name).add_argument("--reason")

TOOLS = ["ddflow_loops", "ddflow_lesson_add", "ddflow_decision_add", "ddflow_research_add",
         "ddflow_bug_found", "ddflow_setup"]
"""
PYPROJECT = '[project]\nname = "x"\n[project.scripts]\nddflow = "x:main"\n'


def repo_with(tmp_path: Path, readme: str, **extra: str) -> Path:
    files = {"README.md": readme, "x.py": CODE, "pyproject.toml": PYPROJECT, **extra}
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def found(report):
    return sorted((f.kind, f.ref) for f in report.findings)


def test_old_readme_yields_exactly_the_three_drifts(tmp_path):
    report = D.check_docs(repo_with(tmp_path, OLD_README))
    assert found(report) == [
        ("anchor", "#the-rules-and-where-each-came-from"),
        ("command", "ddflow setup"),
        ("identifier", "ddflow_research"),
    ]


def test_the_fixed_readme_is_clean(tmp_path):
    report = D.check_docs(repo_with(tmp_path, FIXED_README))
    assert report.clean, found(report)
    assert report.checked["docs"] == ["README.md"]
    assert report.checked["anchors"] == 2


def test_trailing_punctuation_on_a_flag_does_not_hit(tmp_path):
    readme = "Pass `--reason,` or `--reason.` or `--reason`; see `--reason=why`.\n"
    assert D.check_docs(repo_with(tmp_path, readme)).clean


def test_a_missing_flag_is_found(tmp_path):
    report = D.check_docs(repo_with(tmp_path, "Use `--nonesuch`.\n"))
    assert found(report) == [("flag", "--nonesuch")]


def test_links_and_anchors(tmp_path):
    readme = (
        "[ok](docs/a.md#second-part) [gone](docs/missing.md) [bad](docs/a.md#nope)\n"
        "[web](https://example.com/x#y) [self](#top-title) [dup](docs/a.md#second-part-1)\n"
        "`[not](a-link.md)`\n\n```md\n[fenced](nothing.md)\n```\n\n# Top title\n"
    )
    a = "# A\n\n## Second part\n\n## Second part\n\n```\n## in a fence\n```\n"
    report = D.check_docs(repo_with(tmp_path, readme, **{"docs/a.md": a}))
    assert found(report) == [("anchor", "docs/a.md#nope"), ("link", "docs/missing.md")]


def test_slug_follows_github():
    assert D.slugify("The `rules`, and *where* it came") == "the-rules-and-where-it-came"
    assert D.slugify("Phase 160 — the drafter") == "phase-160--the-drafter"
    assert D.anchors_of("# A\n## A\n## A\n") == {"a", "a-1", "a-2"}


def test_history_docs_are_excluded_and_the_doc_set_is_a_parameter(tmp_path):
    root = repo_with(
        tmp_path, "ok\n", **{"CHANGELOG.md": "`ghost_name`\n", "docs/n.md": "`ghost_two`\n"}
    )
    assert sorted(D.check_docs(root).checked["docs"]) == ["README.md", "docs/n.md"]
    assert found(D.check_docs(root)) == [("identifier", "ghost_two")]
    assert found(D.check_docs(root, docs=["CHANGELOG.md"])) == [("identifier", "ghost_name")]
    assert D.check_docs(root, doc_exclude=["docs/**", "CHANGELOG*"]).clean


def test_paths_are_notes_with_an_ignore_list(tmp_path):
    readme = "See `out/records.jsonl` and `x.py` and `runtime/state.db`.\n"
    root = repo_with(tmp_path, readme)
    report = D.check_docs(root)
    assert report.clean
    assert [n.ref for n in report.notes] == ["out/records.jsonl", "runtime/state.db"]
    assert D.check_docs(root, ignore_paths=["out/**", "runtime/*"]).notes == []


def test_ignore_names_and_dotted_names(tmp_path):
    readme = "`ddflow_other` `x.build` `x.gone` `example.com` `a_b.py`\n"
    root = repo_with(tmp_path, readme)
    assert found(D.check_docs(root)) == [("identifier", "ddflow_other"), ("identifier", "x.gone")]
    assert found(D.check_docs(root, ignore_names=["ddflow_*", "x.gone"])) == []


def test_the_digest_is_stable_and_follows_the_report(tmp_path):
    root = repo_with(tmp_path, OLD_README)
    first = D.check_docs(root)
    assert first.digest == D.check_docs(root).digest
    (root / "README.md").write_text(FIXED_README, encoding="utf-8")
    assert D.check_docs(root).digest != first.digest


def test_a_directory_that_is_not_a_repository_cannot_answer(tmp_path):
    with pytest.raises(OSError):
        D.check_docs(tmp_path)


def test_runtime_on_this_repositorys_readme():
    root = Path(__file__).resolve().parents[1]
    start = time.perf_counter()
    report = D.check_docs(root, docs=["README.md"])
    assert time.perf_counter() - start < 2.0
    assert report.checked["identifiers"] > 50


def test_a_named_document_that_cannot_be_read_is_not_a_clean_report(tmp_path):
    root = repo_with(tmp_path, "ok\n")
    with pytest.raises(OSError, match=r"TYPO\.md"):
        D.check_docs(root, docs=["TYPO.md"])


def test_anchor_forms(tmp_path):
    readme = (
        "[a](#my-anchor) [b](#MyAnchor) [c](#setext-title) [d](#sub) [e](#ghost)\n\n"
        '<a name="MyAnchor"></a>\n\nSetext title\n============\n\nSub\n---\n\n'
        "- item\n---\n\nFoo bar\nbaz\n===\n[f](#foo-bar-baz)"
    )
    report = D.check_docs(repo_with(tmp_path, readme))
    assert found(report) == [("anchor", "#ghost"), ("anchor", "#my-anchor")]


def test_a_short_fence_inside_a_long_one_is_code(tmp_path):
    readme = "````\n```\n`ghost_in_code` [x](nothing.md)\n```\n````\n\n`ghost_after`\n"
    assert found(D.check_docs(repo_with(tmp_path, readme))) == [("identifier", "ghost_after")]


def test_footnotes_and_emphasised_setext_headings(tmp_path):
    readme = (
        "[^1]: See the [install](docs/a.md) page.\n[ref]: docs/a.md#second-part\n"
        "[bad]: docs/gone.md\n\n**Install**\n===\n\n[i](#install)\n"
    )
    report = D.check_docs(repo_with(tmp_path, readme, **{"docs/a.md": "## Second part\n"}))
    assert found(report) == [("link", "docs/gone.md")]
