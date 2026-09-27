"""B18: a generated file must come back byte-identical when regenerated.

`write_views` claimed "same state in, same bytes out" and nothing held it to that; and
the pre-commit hook listed `docs/ddflow/` as self-managed, so a hand-edited or stale view
was committed with no check at all. A view that disagrees with the log is worse than no
view: it is the document people read INSTEAD of the log.

End-to-end through a real `git commit` and the installed hook, because the hook is the
only place this is enforced and a unit test of the comparison would not show that the
hook calls it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

VIEW = "docs/ddflow/QUEUE.md"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=180
    )


def _commit(repo: Path, *paths: str) -> subprocess.CompletedProcess:
    _git(repo, "add", *paths)
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "views"],
        capture_output=True,
        text=True,
        env={**os.environ},
        timeout=180,
    )


def _config(repo: Path, mode: str | None) -> None:
    # The lease policy is OFF so that nothing but the view check can refuse.
    body = '[enforce]\ncommit_without_lease = "off"\n'
    if mode:
        body += f'generated_views = "{mode}"\n'
    (repo / ".ddflow" / "config.toml").write_text(body)


@pytest.fixture
def adopted(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    _config(repo, None)
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/a.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scaffold", "--no-verify")
    return repo


def test_freshly_rendered_views_commit_cleanly(adopted):
    assert run_cli(adopted, "render")[0] == 0
    r = _commit(adopted, "docs/ddflow")
    assert r.returncode == 0, f"a correct view was refused:\n{r.stderr}"


def test_a_hand_edited_view_is_refused_and_named(adopted):
    run_cli(adopted, "render")
    view = adopted / VIEW
    view.write_text(view.read_text() + "\n- [x] P9 something that never happened\n")
    r = _commit(adopted, VIEW)
    assert r.returncode != 0, "a hand-edited generated view was committed"
    assert VIEW in r.stderr, "the refusal must name the file"
    assert "ddflow render" in r.stderr, "the refusal must name the remedy"


def test_a_stale_view_is_refused(adopted):
    """The ordinary way a view goes wrong: rendered, then the queue moved on."""
    run_cli(adopted, "render")
    run_cli(adopted, "task", "add", "P1.T2", "--phase", "P1", "--globs", "src/b.py")
    r = _commit(adopted, VIEW)
    assert r.returncode != 0, "a view that no longer matches the log was committed"


def test_the_index_is_what_is_checked_not_the_working_copy(adopted):
    """The commit writes the STAGED bytes. Checking the working copy instead passes a
    commit whose file is wrong, as long as the file on disk has since been fixed."""
    run_cli(adopted, "render")
    view = adopted / VIEW
    good = view.read_text()
    view.write_text(good + "\nhand edit\n")
    _git(adopted, "add", VIEW)
    view.write_text(good)  # fixed on disk, NOT re-staged
    r = _commit(adopted)
    assert r.returncode != 0, "checked the working copy; the staged, edited bytes got in"


def test_a_view_rendered_elsewhere_is_still_a_generated_file(adopted):
    """`render --out` puts views anywhere. B18's criterion is 'marked generated', not
    'lives in docs/ddflow', or a view one directory over is unchecked."""
    assert run_cli(adopted, "render", "--out", "handbook")[0] == 0
    view = adopted / "handbook" / "QUEUE.md"
    view.write_text(view.read_text().replace("P1.T1", "P1.T7"))
    r = _commit(adopted, "handbook/QUEUE.md")
    assert r.returncode != 0, "an edited view outside docs/ddflow was committed"


@pytest.mark.parametrize(("mode", "rc", "says"), [("warn", 0, True), ("off", 0, False)])
def test_the_policy_knob(adopted, mode, rc, says):
    _config(adopted, mode)
    run_cli(adopted, "render")
    view = adopted / VIEW
    view.write_text(view.read_text() + "\nhand edit\n")
    r = _commit(adopted, VIEW, ".ddflow/config.toml")
    assert r.returncode == rc, r.stderr
    assert (VIEW in r.stderr) is says, r.stderr


def test_an_ordinary_file_that_quotes_the_marker_is_not_a_view(adopted):
    """The marker alone is not enough: this test file quotes it, and so might a doc."""
    from ddflow.views.markdown import GENERATED

    (adopted / "notes").mkdir()
    (adopted / "notes" / "QUEUE.md").write_text("our own queue notes\n")
    (adopted / "notes" / "about.md").write_text(GENERATED + "\nprose about the marker\n")
    r = _commit(adopted, "notes")
    assert r.returncode == 0, r.stderr


def test_rendering_is_deterministic_and_the_bundle_writes_the_same_bytes(adopted, tmp_path):
    """ "Same state in, same bytes out" -- and ONE view map. `sessions.bundle` said it
    used the same map as `write_views` while holding its own copy, which already wrote
    different bytes (no trailing-newline normalisation)."""
    from ddflow.config import Config
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog
    from ddflow.services.sessions import bundle
    from ddflow.views.markdown import VIEWS, write_views

    cfg = Config.load(adopted)
    state = fold(EventLog(adopted, "t").read_all())
    a = {p.name: p.read_bytes() for p in write_views(tmp_path / "a", state, cfg, subdir=".")}
    state2 = fold(EventLog(adopted, "t").read_all())
    b = {p.name: p.read_bytes() for p in write_views(tmp_path / "b", state2, cfg, subdir=".")}
    assert a == b, "two renders of the same log differ"
    assert set(a) == {name for name, _fn in VIEWS}
    bundled = {p.name: p.read_bytes() for p in bundle(state, [], tmp_path / "c", cfg)}
    for name, data in a.items():
        assert bundled[name] == data, f"bundle wrote different bytes for {name}"


def test_an_env_override_at_render_time_does_not_make_a_correct_view_stale(adopted, monkeypatch):
    """`board` reads config, and config honours `DDFLOW_*` env overrides. An agent whose
    MCP server carried one rendered, then committed from a shell without it, and was
    refused with a message blaming a hand edit. Found by an adversarial review; a view
    that depends on whoever's environment rendered it is not reproducible, which is the
    property B18 exists to hold. Written views take config from the FILES only."""
    monkeypatch.setenv("DDFLOW_GATES_TASK_PIPELINE", "tests,review")
    assert run_cli(adopted, "render")[0] == 0
    monkeypatch.delenv("DDFLOW_GATES_TASK_PIPELINE")
    r = _commit(adopted, "docs/ddflow")
    assert r.returncode == 0, f"a correct render was refused over an env override:\n{r.stderr}"


def test_crlf_line_endings_are_not_a_content_change(adopted):
    """`write_text` writes CRLF on Windows, and with `core.autocrlf=false` git stages it
    as is. Same content, different bytes: refusing it would fail every Windows commit."""
    run_cli(adopted, "render")
    view = adopted / VIEW
    view.write_bytes(view.read_bytes().replace(b"\n", b"\r\n"))
    _git(adopted, "config", "core.autocrlf", "false")
    r = _commit(adopted, VIEW)
    assert r.returncode == 0, f"CRLF was treated as a hand edit:\n{r.stderr}"


def test_an_env_override_at_commit_time_does_not_make_a_correct_view_stale(adopted, monkeypatch):
    """The other half: the CHECK must not read the committer's environment either."""
    assert run_cli(adopted, "render")[0] == 0
    monkeypatch.setenv("DDFLOW_GATES_TASK_PIPELINE", "tests,review")
    r = _commit(adopted, "docs/ddflow")
    assert r.returncode == 0, f"a correct render was refused over an env override:\n{r.stderr}"
