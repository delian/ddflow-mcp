"""A completed item that changes a config or event declaration must announce it (B84a7e5b17e).

`ddflow verify <id>` re-derives the claims behind a completion. A landing that changed a
`ddflow/config_sections/` file or the event vocabulary and left the upgrade manifest out of
sync used to hold silently: the knob or event kind only reddened the upgrade-manifest tests
later, on the merged branch. The `manifest` claim fails the completion instead.
"""

from __future__ import annotations

from conftest import run_cli
from helpers import git as _git

from ddflow.api.verify import verify
from ddflow.services import upgrade_manifest as UM
from ddflow.services import upgrade_registry as UR


def _commit(repo, files: dict[str, str], msg="work"):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _claims(out):
    return {c["id"]: c for c in out.data["claims"]}


def test_a_landing_that_adds_an_unannounced_knob_fails(repo, monkeypatch):
    run_cli(repo, "init")
    run_cli(
        repo,
        "task",
        "add",
        "T1",
        "--title",
        "add a knob",
        "--globs",
        "ddflow/config_sections/locks.py",
    )
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    # The landing changes a config declaration and adds a knob the manifest never heard of.
    sha = _commit(repo, {"ddflow/config_sections/locks.py": "# a config section\n"})
    drift = {**UM.knob_defaults(), "locks.fresh_knob": 3}
    monkeypatch.setattr(UM, "knob_defaults", lambda cfg=None: dict(drift))
    monkeypatch.setattr(UR, "fresh", lambda: (dict(drift), UM.event_kinds()))
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    claims = _claims(verify(repo, "T1"))
    assert claims["manifest"]["status"] == "fail"
    assert "locks.fresh_knob" in claims["manifest"]["detail"]


def test_a_landing_that_does_not_touch_a_declaration_has_no_manifest_claim(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "edit a plain file", "--globs", "w.py")
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    sha = _commit(repo, {"w.py": "x = 1\n"})
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    # No config or event declaration changed, so the manifest claim is not owed at all.
    assert "manifest" not in _claims(verify(repo, "T1"))


def test_a_landing_that_touches_a_declaration_but_keeps_the_manifest_clean_passes(repo):
    run_cli(repo, "init")
    run_cli(
        repo,
        "task",
        "add",
        "T1",
        "--title",
        "edit a declaration cleanly",
        "--globs",
        "ddflow/config_sections/locks.py",
    )
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    sha = _commit(
        repo, {"ddflow/config_sections/locks.py": "# a config section, unchanged knobs\n"}
    )
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    # The tree's knobs/kinds still replay from the manifest, so the landing is announced.
    claims = _claims(verify(repo, "T1"))
    assert claims["manifest"]["status"] == "ok"


def test_a_landing_is_not_charged_with_drift_it_does_not_declare(repo, monkeypatch):
    run_cli(repo, "init")
    run_cli(
        repo,
        "task",
        "add",
        "T1",
        "--title",
        "edit a declaration",
        "--globs",
        "ddflow/config_sections/locks.py",
    )
    _commit(repo, {"seed.txt": "s\n"}, "seed")
    sha = _commit(repo, {"ddflow/config_sections/locks.py": "# a config section\n"})
    # Drift in a section THIS landing did not touch is a warning, not this landing's failure.
    drift = {**UM.knob_defaults(), "review.fresh_knob": 3}
    monkeypatch.setattr(UM, "knob_defaults", lambda cfg=None: dict(drift))
    monkeypatch.setattr(UR, "fresh", lambda: (dict(drift), UM.event_kinds()))
    run_cli(repo, "complete", "T1", "--sha", sha, "--force")
    claims = _claims(verify(repo, "T1"))
    assert claims["manifest"]["status"] == "warn"
    assert "review.fresh_knob" in claims["manifest"]["detail"]
