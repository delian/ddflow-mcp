"""The ``changelog`` export kind (B-export-changelog): Keep a Changelog from a synthetic log
and a temp git repo with version tags."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.core.events import Event, parse_changelog
from ddflow.services.export import (
    EXIT_REFUSED,
    EXIT_UNAVAILABLE,
    ExportError,
    Filters,
    query,
    registry,
    write,
)
from ddflow.services.export import kind_changelog as kc

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(repo: Path, *args: str, date: str = "2026-09-01T12:00:00+00:00") -> str:
    import os

    env = {**os.environ, **ENV, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def commit(repo: Path, name: str, message: str, date: str) -> str:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-m", message, date=date)
    return git(repo, "rev-parse", "HEAD")


class Repo(type(Path())):  # a Path that also carries the commit shas of the fixture
    shas: dict[str, str]


@pytest.fixture
def repo(tmp_path_factory):
    """c0 (old work) @ v1.0.0 (09-10) ; c1, c2, c3 @ v1.1.0 (09-20) ; c4, c5 after it."""
    tmp_path = Repo(tmp_path_factory.mktemp("repo"))
    git(tmp_path, "init", "-q", "-b", "main")
    shas = {"c0": commit(tmp_path, "a", "old work", "2026-09-05T10:00:00+00:00")}
    git(tmp_path, "tag", "-a", "v1.0.0", "-m", "v1.0.0", date="2026-09-10T12:00:00+00:00")
    shas["c1"] = commit(tmp_path, "b", "merge B-b: bee", "2026-09-12T10:00:00+00:00")
    shas["c2"] = commit(tmp_path, "c", "fix: repair the thing", "2026-09-13T10:00:00+00:00")
    shas["c3"] = commit(tmp_path, "d", "internal tidy", "2026-09-14T10:00:00+00:00")
    git(tmp_path, "tag", "-a", "v1.1.0", "-m", "v1.1.0", date="2026-09-20T12:00:00+00:00")
    shas["c4"] = commit(tmp_path, "e", "feat: shiny", "2026-09-25T10:00:00+00:00")
    shas["c5"] = commit(tmp_path, "f", "merge B-f: eff", "2026-09-26T10:00:00+00:00")
    (tmp_path / ".ddflow" / "events").mkdir(parents=True)
    tmp_path.shas = shas
    return tmp_path


def ev(name: str, subject: str, ts: str, **data) -> Event:
    return Event(kind=name, subject=subject, data=data, ts=ts, agent="a", lamport=0)


def task(i: str, title: str, completed: str, sha: str = "", tags=(), **extra) -> list[Event]:
    out = [ev("task.added", i, "2026-09-01T00:00:00Z", title=title, kind="task", tags=list(tags))]
    out.append(ev("item.completed", i, completed, sha=sha, **extra))
    return out


def load(repo: Path, events: list[Event]) -> query.Query:
    q = query.build(events)
    q.repo = repo
    return q


def render(q: query.Query, **filters) -> str:
    return registry.render_body("changelog", q, Filters(**filters))


def cl(text: str) -> dict:
    return {"changelog": parse_changelog(text)}


def events(shas: dict[str, str]) -> list[Event]:
    evs: list[Event] = []
    evs += task("B-old", "Old work. More words", "2026-09-06T00:00:00Z", shas["c0"])
    evs += task("B-b", "Bee gets wings. Second sentence here", "2026-09-12T00:00:00Z", shas["c1"])
    evs += task("B-c", "Repair the thing (fixes bug Bx1)", "2026-09-13T00:00:00Z", shas["c2"])
    evs += task("B-d", "Internal tidy", "2026-09-14T00:00:00Z", shas["c3"], **cl("skip"))
    evs += task("B-e", "Shiny things", "2026-09-25T00:00:00Z", shas["c4"])
    evs += task(
        "B-f", "Eff gets removed", "2026-09-26T00:00:00Z", shas["c5"], **cl("Removed: Eff is gone")
    )
    # no merged_sha: completed between the tags -> placed by date into 1.1.0
    evs += task("B-g", "Gee whiz", "2026-09-15T00:00:00Z", "", tags=["feature"])
    return evs


GOLDEN = """# Changelog

All notable changes to this project are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Generated from the event log and the version tags: versions up to and including v1.0.0 are not reconstructed.

## [Unreleased]

### Added

- Shiny things

### Removed

- Eff is gone

## [1.1.0] - 2026-09-20

### Added

- Gee whiz _(placed by date, no merge commit)_

### Changed

- Bee gets wings

### Fixed

- Repair the thing

Entries marked "placed by date" have no merge commit recorded, so their version comes from when they were completed, not from git history.
"""


def test_two_tags_expected_sections(repo):
    q = load(repo, events(repo.shas))
    assert render(q) == GOLDEN


def test_skip_suppresses_and_baseline_stays_out(repo):
    body = render(load(repo, events(repo.shas)))
    assert "tidy" not in body.lower()
    assert "Old work" not in body and "[1.0.0]" not in body


def test_fallback_is_labelled_only_on_the_fallback_entry(repo):
    body = render(load(repo, events(repo.shas)))
    assert body.count("placed by date, no merge commit") == 1
    assert "Bee gets wings _(" not in body


def test_category_order_field_then_commit_then_tags_then_suffix_then_changed(repo):
    shas = repo.shas
    evs = []
    # field beats the "fix:" commit prefix
    evs += task("T1", "one", "2026-09-27T00:00:00Z", shas["c2"], **cl("Security: patched"))
    # commit prefix feat beats the breaking tag
    evs += task("T2", "two", "2026-09-27T00:00:00Z", shas["c4"], tags=["breaking"])
    # tags beat the title suffix: breaking -> Changed
    evs += task("T3", "three (fixes bug B9)", "2026-09-27T00:00:00Z", shas["c1"], tags=["breaking"])
    # suffix alone -> Fixed
    evs += task("T4", "four (fixes bug B8)", "2026-09-27T00:00:00Z", shas["c3"])
    # nothing -> Changed
    evs += task("T5", "five", "2026-09-27T00:00:00Z", shas["c5"])
    data = kc.data(load(repo, evs), Filters())
    got = {
        e["id"]: g["category"] for s in data["sections"] for g in s["groups"] for e in g["entries"]
    }
    assert got == {
        "T1": "Security",
        "T2": "Added",
        "T3": "Changed",
        "T4": "Fixed",
        "T5": "Changed",
    }


def test_line_strips_suffix_and_takes_first_sentence(repo):
    data = kc.data(load(repo, events(repo.shas)), Filters())
    lines = {
        e["id"]: e["line"] for s in data["sections"] for g in s["groups"] for e in g["entries"]
    }
    assert lines["B-c"] == "Repair the thing"
    assert lines["B-b"] == "Bee gets wings"


def test_bug_with_explicit_line_listed_unless_a_task_names_it(repo):
    evs = events(repo.shas)
    evs += [
        ev("bug.found", "Bx1", "2026-09-11T00:00:00Z", summary="x"),
        ev("bug.fixed", "Bx1", "2026-09-13T00:00:00Z", **cl("Fixed: from the bug")),
        ev("bug.found", "By2", "2026-09-27T00:00:00Z", summary="y"),
        ev("bug.fixed", "By2", "2026-09-28T00:00:00Z", **cl("Security: closed a hole")),
    ]
    body = render(load(repo, evs))
    assert "from the bug" not in body  # task B-c names Bx1: the task speaks
    assert "- Repair the thing" in body
    assert "closed a hole _(placed by date, no merge commit)_" in body  # after v1.1.0


def test_version_slice_is_release_notes_without_file_header(repo):
    q = load(repo, events(repo.shas))
    for want in ("1.1.0", "v1.1.0"):
        body = render(q, tag=want)
        assert body.startswith("## [1.1.0] - 2026-09-20\n")
        assert "# Changelog" not in body and "Unreleased" not in body
        assert "- Repair the thing" in body
    with pytest.raises(ExportError) as e:
        render(q, tag="9.9.9")
    assert e.value.code == EXIT_REFUSED


def test_unreleased_region_round_trips(repo):
    q = load(repo, events(repo.shas))
    body = render(q, tag="unreleased")
    target = repo / "CHANGELOG.md"
    hand = "# Changelog\n\nHand written intro.\n\n"
    tail = "\n## [1.0.0]\n\nHand kept history.\n"
    markers = (
        "<!-- ddflow:begin doc=changelog body-sha256=000000000000 -->\n"
        "<!-- ddflow:end doc=changelog -->\n"
    )
    target.write_text(hand + markers + tail)
    r1 = write.write_region(repo, "CHANGELOG.md", "changelog", body, force=True)
    assert r1.action == "updated"
    text = target.read_text()
    assert text.startswith(hand) and text.endswith(tail)
    assert "- Shiny things" in text and "- Eff is gone" in text
    r2 = write.write_region(repo, "CHANGELOG.md", "changelog", render(q, tag="unreleased"))
    assert r2.action == "unchanged" and target.read_text() == text
    # a new completed item changes only the region
    more = events(repo.shas) + task("B-h", "Hotel", "2026-09-27T00:00:00Z", repo.shas["c5"])
    r3 = write.write_region(
        repo, "CHANGELOG.md", "changelog", render(load(repo, more), tag="unreleased")
    )
    assert r3.action == "updated"
    new = target.read_text()
    assert new.startswith(hand) and new.endswith(tail) and "- Hotel" in new


def test_append_mode_adds_only_entries_after_last(repo):
    evs = events(repo.shas)
    q = load(repo, evs)
    produce = kc.append_unreleased(q)
    text, last = produce("")
    assert "- Added: Shiny things\n" in text and "- Removed: Eff is gone\n" in text
    assert "Repair" not in text  # released in 1.1.0
    again, _ = produce(last)
    assert again == ""
    q2 = load(repo, evs + task("B-h", "Hotel", "2026-09-27T00:00:00Z", repo.shas["c5"]))
    more, _ = kc.append_unreleased(q2)(last)
    assert more == "- Changed: Hotel\n"


def test_compare_links_from_origin_and_tags(repo):
    git(repo, "remote", "add", "origin", "git@example.com:acme/proj.git")
    body = render(load(repo, events(repo.shas)))
    assert body.endswith(
        "[Unreleased]: https://example.com/acme/proj/compare/v1.1.0...HEAD\n"
        "[1.1.0]: https://example.com/acme/proj/compare/v1.0.0...v1.1.0\n"
    )


def test_git_unreadable_is_could_not_run_not_an_empty_changelog(tmp_path):
    (tmp_path / ".ddflow" / "events").mkdir(parents=True)  # not a git repository
    with pytest.raises(ExportError) as e:
        render(load(tmp_path, []))
    assert e.value.code == EXIT_UNAVAILABLE
    with pytest.raises(ExportError) as e2:
        render(query.build([]))  # no repo at all
    assert e2.value.code == EXIT_UNAVAILABLE


def test_same_log_same_bytes(repo):
    evs = events(repo.shas)
    assert render(load(repo, evs)) == render(load(repo, list(reversed(evs))))


def test_hotfix_landed_via_branch_second_parent_is_found(repo):
    """reached() is reused: a landing whose merge commit is on main only is found by its
    branch tip on another line -- here, plain ancestry of a merge commit's tip."""
    git(repo, "checkout", "-q", "-b", "feat", "v1.0.0")
    tip = commit(repo, "g", "feat: topic", "2026-09-11T10:00:00+00:00")
    git(repo, "checkout", "-q", "main")
    git(
        repo, "merge", "--no-ff", "-m", "merge B-t: topic", "feat", date="2026-09-15T10:00:00+00:00"
    )
    m = git(repo, "rev-parse", "HEAD")
    evs = task("B-t", "Topic", "2026-09-15T00:00:00Z", m)
    evs.append(
        ev(
            "worktree.merged",
            "B-t",
            "2026-09-15T00:00:00Z",
            sha=m,
            landed_before=repo.shas["c3"],
            landed_after=m,
        )
    )
    assert tip != m
    got = kc.data(load(repo, evs), Filters())
    names = {
        e["id"]: s["title"] for s in got["sections"] for g in s["groups"] for e in g["entries"]
    }
    assert names == {"B-t": "Unreleased"}  # merged after v1.1.0 was cut from main


def test_registered_and_defaults():
    k = registry.get("changelog")
    assert k.default_target == "CHANGELOG.md" and k.filters == frozenset({"tag"})


def test_no_tags_means_no_baseline_and_everything_is_unreleased(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    sha = commit(tmp_path, "a", "feat: first", "2026-09-05T10:00:00+00:00")
    (tmp_path / ".ddflow" / "events").mkdir(parents=True)
    body = render(load(tmp_path, task("B-1", "First thing", "2026-09-06T00:00:00Z", sha)))
    assert "## [Unreleased]\n\n### Added\n\n- First thing\n" in body
    assert "are not reconstructed" not in body and "[Unreleased]:" not in body


def test_skipped_task_does_not_swallow_its_bugs_own_line(repo):
    evs = task(
        "T-s",
        "Internal repair (fixes bug Bz9)",
        "2026-09-27T00:00:00Z",
        repo.shas["c5"],
        **cl("skip"),
    )
    evs += [
        ev("bug.found", "Bz9", "2026-09-26T00:00:00Z", summary="z"),
        ev("bug.fixed", "Bz9", "2026-09-28T00:00:00Z", **cl("Fixed: crash on empty input")),
    ]
    body = render(load(repo, evs), tag="unreleased")
    assert "crash on empty input" in body and "Internal repair" not in body


def test_branch_tip_as_merged_sha_is_placed_by_ancestry(repo):
    git(repo, "checkout", "-q", "-b", "feat", "v1.0.0")
    tip = commit(repo, "g", "feat: topic", "2026-09-11T10:00:00+00:00")
    git(repo, "checkout", "-q", "main")
    git(
        repo, "merge", "--no-ff", "-m", "merge B-t: topic", "feat", date="2026-09-15T10:00:00+00:00"
    )
    git(repo, "tag", "-a", "v1.2.0", "-m", "v1.2.0", date="2026-09-30T12:00:00+00:00")
    got = kc.data(load(repo, task("B-t", "Topic", "2026-09-12T00:00:00Z", tip)), Filters())
    placed = {
        e["id"]: (s["title"], e["by_date"])
        for s in got["sections"]
        for g in s["groups"]
        for e in g["entries"]
    }
    assert placed == {"B-t": ("1.2.0", False)}
