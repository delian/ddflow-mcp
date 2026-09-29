"""pre-commit as a companion: ddflow proposes a configuration for THIS repository.

Operator, 2026-09-28: "propose to the agent as a companion pre-commit with good
configurations that are relevant for the project". The proposal is read from the stacks
the repository actually has, pinned, and carries ddflow's own checks as local hooks so
the framework and ddflow do not fight over `.git/hooks/` (research Rb5e33fdbf9).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ddflow.api import operations as OPS
from ddflow.services import companions as C
from ddflow.services import precommit as PC

REFUSED = 3


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def mixed(repo):
    files = {
        "pyproject.toml": '[project]\nname = "x"\n',
        "src/app.py": "def f():\n    return 1\n",
        "scripts/run.sh": "#!/bin/sh\necho hi\n",
        "Dockerfile": "FROM python:3.13-slim\n",
        "web/package.json": "{}\n",
        "package.json": json.dumps({"devDependencies": {"eslint": "^9"}}),
        "config.yaml": "a: 1\n",
    }
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "mixed")
    return repo


def _hooks(p: PC.Proposal) -> dict[str, list[str]]:
    return {r.url: [h.id for h in r.hooks] for r in p.repos}


def test_the_stacks_are_read_from_the_repository_not_assumed():
    got = PC.detect_stacks(["a/b.py", "Dockerfile", "x/run.sh", "go.mod", "README.md"])
    assert set(got) == {"python", "docker", "shell", "go"}
    assert PC.detect_stacks(["README.md", "docs/a.md"]) == {}


def test_a_mixed_repository_gets_the_hooks_for_each_of_its_stacks(mixed):
    p = PC.propose(mixed)
    hooks = _hooks(p)
    assert {"python", "shell", "docker", "javascript"} <= set(p.stacks)
    assert hooks["https://github.com/astral-sh/ruff-pre-commit"] == ["ruff-check", "ruff-format"]
    assert hooks["https://github.com/shellcheck-py/shellcheck-py"] == ["shellcheck"]
    assert hooks["https://github.com/hadolint/hadolint"] == ["hadolint-docker"]
    assert "check-yaml" in hooks["https://github.com/pre-commit/pre-commit-hooks"]
    assert "check-json" in hooks["https://github.com/pre-commit/pre-commit-hooks"]
    assert hooks["https://github.com/gitleaks/gitleaks"] == ["gitleaks"]
    local = hooks["local"]
    assert "eslint" in local, "package.json declares it"
    assert "prettier" not in local and any("prettier" in s for s in p.skipped), (
        "a tool the project does not declare is not invented -- and the gap is said"
    )
    assert local[-2:] == ["ddflow-check-commit", "ddflow-check-msg"]


def test_a_python_only_repository_gets_no_other_stacks_tools(repo):
    (repo / "m.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "py")
    hooks = _hooks(PC.propose(repo))
    assert "https://github.com/hadolint/hadolint" not in hooks
    assert "https://github.com/shellcheck-py/shellcheck-py" not in hooks
    assert "check-json" not in hooks["https://github.com/pre-commit/pre-commit-hooks"]


def test_every_remote_hook_is_pinned(mixed):
    for repo in PC.propose(mixed).repos:
        if repo.url != "local":
            assert repo.rev.startswith("v"), f"{repo.url} is unpinned"
            assert f"rev: {repo.rev}" in PC.render(PC.propose(mixed))


def test_ddflows_own_checks_run_inside_the_framework(mixed):
    text = PC.propose(mixed, ddflow_cmd="uvx --from ddflow-mcp ddflow").text
    assert "default_install_hook_types: [pre-commit, commit-msg]" in text
    assert 'entry: "uvx --from ddflow-mcp ddflow hooks check-commit"' in text
    assert 'stages: ["commit-msg"]' in text


@pytest.mark.skipif(shutil.which("pre-commit") is None, reason="pre-commit is not installed")
def test_the_proposal_is_a_config_pre_commit_itself_accepts(mixed, tmp_path):
    cfg = tmp_path / "proposed.yaml"
    cfg.write_text(PC.propose(mixed).text)
    r = subprocess.run(["pre-commit", "validate-config", str(cfg)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_write_creates_the_file_and_never_replaces_one(mixed):
    out = OPS.precommit(mixed, where=mixed, write=True)
    assert out.exit == 0 and out.data["written"] is True
    path = mixed / ".pre-commit-config.yaml"
    assert path.read_text() == out.data["text"]
    path.write_text("repos: []  # the operator's own\n")
    again = OPS.precommit(mixed, where=mixed, write=True)
    assert again.exit == REFUSED and again.data["written"] is False
    assert path.read_text() == "repos: []  # the operator's own\n"


def test_the_proposal_alone_writes_nothing(mixed):
    out = OPS.precommit(mixed, where=mixed)
    assert out.exit == 0 and out.data["exists"] is False
    assert not (mixed / ".pre-commit-config.yaml").exists()


def test_the_catalogue_recommends_it():
    comp = {c.id: c for c in C.load(Path(__file__).resolve().parents[1])}["pre-commit"]
    assert comp.kind == "cli" and "standards" in comp.gates and comp.default is True
    assert "ddflow precommit" in comp.note, "the recommendation names how to get the config"


def test_a_python_and_docker_repository_gets_ruff_hadolint_and_both_ddflow_hooks(repo):
    """The spec's own example."""
    (repo / "app.py").write_text("x = 1\n")
    (repo / "Dockerfile").write_text("FROM python:3.13-slim\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "py+docker")
    text = PC.propose(repo).text
    for needle in ("id: ruff-check", "id: ruff-format", "id: hadolint-docker",
                   "id: ddflow-check-commit", "id: ddflow-check-msg"):  # fmt: skip
        assert needle in text, needle


def test_json_with_comments_is_not_checked_as_strict_json():
    """tsconfig.json and editor settings carry comments; strict check-json refuses them,
    so a TypeScript project would fail its first commit."""
    import re

    for jsonc in ("tsconfig.json", "web/tsconfig.base.json", "jsconfig.json",
                  ".vscode/settings.json", ".devcontainer/devcontainer.json"):  # fmt: skip
        assert re.search(PC.JSONC, jsonc), jsonc
    for strict in ("package.json", "data/config.json", "vscode.json"):
        assert not re.search(PC.JSONC, strict), strict


def test_check_json_excludes_jsonc_and_check_yaml_accepts_multi_document_files(mixed):
    hooks = {h.id: h for r in PC.propose(mixed).repos for h in r.hooks}
    assert hooks["check-json"].fields["exclude"] == PC.JSONC
    assert "--allow-multiple-documents" in hooks["check-yaml"].fields["args"]


def test_gofmt_rewrites_so_pre_commit_sees_the_change(repo):
    """gofmt's exit status does not report unformatted files; pre-commit fails a hook
    that modifies one. `-l -d` alone would pass everything."""
    (repo / "go.mod").write_text("module x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "go")
    gofmt = {h.id: h for r in PC.propose(repo).repos for h in r.hooks}["gofmt"]
    assert "-w" in gofmt.fields["entry"].split()


def test_a_ddflow_command_that_cannot_be_found_is_reported(mixed):
    missing = OPS.precommit(mixed, where=mixed, ddflow_cmd="no-such-ddflow-xyz")
    assert missing.exit == 0 and missing.data["ddflow_cmd_found"] is False
    script = mixed / "scripts" / "ddflow_hook.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    local = OPS.precommit(mixed, where=mixed, ddflow_cmd="scripts/ddflow_hook.sh")
    assert local.data["ddflow_cmd_found"] is True, "a relative entry is read from the repo"
    assert 'entry: "scripts/ddflow_hook.sh hooks check-msg"' in local.data["text"]


def test_an_empty_ddflow_command_is_refused(mixed):
    out = OPS.precommit(mixed, where=mixed, ddflow_cmd="  ")
    assert out.exit == REFUSED


def test_write_never_follows_a_dangling_symlink(mixed, tmp_path):
    target = tmp_path / "elsewhere.yaml"
    (mixed / ".pre-commit-config.yaml").symlink_to(target)
    out = OPS.precommit(mixed, where=mixed, write=True)
    assert out.exit == REFUSED and not target.exists()


# -- findings of the rubber_duck review ----------------------------------------------------


def _commit(repo: Path, files: dict[str, str]) -> Path:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "files")
    return repo


def test_git_unable_to_list_the_files_is_could_not_run(repo, monkeypatch):
    """Bug B4894725af0: exit 1 said the proposal FAILED; nothing was proposed at all."""
    monkeypatch.setattr(PC, "propose", lambda *a, **k: None)
    assert OPS.precommit(repo, where=repo).exit == 2


def test_whole_project_checks_run_before_a_push_not_every_commit(repo):
    """Bug Be21580696b: `go vet ./...` and `cargo clippy -D warnings` re-check the whole
    project, and at pre-commit they block every commit of a repo with old warnings."""
    _commit(repo, {"go.mod": "module x\n", "Cargo.toml": "[package]\nname='x'\n"})
    p = PC.propose(repo)
    hooks = {h.id: h for r in p.repos for h in r.hooks}
    assert hooks["go-vet"].fields["stages"] == ["pre-push"]
    assert hooks["cargo-clippy"].fields["stages"] == ["pre-push"]
    assert "stages" not in hooks["gofmt"].fields, "a per-file check stays at pre-commit"
    assert "default_install_hook_types: [pre-commit, commit-msg, pre-push]" in p.text


def test_no_pre_push_hook_is_installed_when_nothing_runs_there(mixed):
    assert "default_install_hook_types: [pre-commit, commit-msg]\n" in PC.propose(mixed).text


def test_a_program_a_proposed_hook_needs_and_this_machine_lacks_is_named(mixed, monkeypatch, tmp_path):  # fmt: skip
    """Bug B3d0cdb15f3: hadolint-docker needs docker, eslint needs npx; absent, every
    commit fails and it reads like a refusal."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(bin_))
    out = OPS.precommit(mixed, where=mixed)
    assert {"docker", "npx"} <= set(out.data["missing"]), out.data["missing"]
    assert "git" not in out.data["missing"]


def test_yaml_is_fully_loaded_unless_the_project_uses_custom_tags(repo):
    """Bug B7159db7e82: --unsafe parses only, losing what a full load catches (duplicate
    keys) -- for every repository, to suit the few whose YAML carries custom tags."""
    plain = _commit(repo, {"config.yaml": "a: 1\n"})
    args = {h.id: h for r in PC.propose(plain).repos for h in r.hooks}["check-yaml"].fields["args"]
    assert "--unsafe" not in args and "--allow-multiple-documents" in args
    _commit(repo, {"mkdocs.yml": "site_name: x\n"})
    args = {h.id: h for r in PC.propose(repo).repos for h in r.hooks}["check-yaml"].fields["args"]
    assert "--unsafe" in args, "MkDocs configs use !!python/name tags"


def test_the_activation_hint_names_the_hook_types_the_config_declares(repo):
    """Bug Bed4fd7ba0c: the hint said "the pre-commit and commit-msg hooks" whatever the
    config declared, and a Go project's config also installs pre-push."""
    _commit(repo, {"go.mod": "module x\n"})
    out = OPS.precommit(repo, where=repo)
    assert out.data["hook_types"] == ["pre-commit", "commit-msg", "pre-push"]


# -- reachable from both surfaces ---------------------------------------------------------


def test_the_cli_command_proposes_writes_and_refuses_end_to_end(mixed):
    """The API passing its tests said nothing about whether anyone could invoke it: the
    command body existed for a while with no parser entry (rubber_duck review)."""
    from conftest import run_cli

    code, out, err = run_cli(mixed, "precommit")
    assert code == 0, err
    assert "id: ddflow-check-commit" in out and "--write creates it" in out
    assert not (mixed / ".pre-commit-config.yaml").exists()
    code, out, err = run_cli(mixed, "precommit", "--write", "--ddflow-cmd", "scripts/run.sh")
    assert code == 0 and "Wrote" in out, err
    assert 'entry: "scripts/run.sh hooks check-commit"' in (mixed / ".pre-commit-config.yaml").read_text()  # fmt: skip
    code, _out, err = run_cli(mixed, "precommit", "--write")
    assert code == REFUSED and "not replaced" in err


def test_the_mcp_tool_returns_the_same_proposal(mixed):
    from ddflow.surfaces.mcp import TOOLS

    tool = TOOLS["ddflow_precommit"]
    assert set(tool["properties"]) == {"ddflow_cmd", "write"}
    out = tool["api"](mixed, {"ddflow_cmd": "x"}, "", called_from=mixed)
    assert out.exit == 0 and 'entry: "x hooks check-msg"' in out.data["text"]


def test_a_write_that_fails_partway_leaves_no_config_behind(mixed, monkeypatch):
    """Bug B985505b520: a truncated file pre-commit would run, and that every later
    --write refuses to replace because it exists."""
    import errno
    import os

    real_open = Path.open

    class HalfWriter:
        def __init__(self, fh):
            self.fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.fh.close()
            return False

        def write(self, text):
            self.fh.write(text[: len(text) // 2])
            raise OSError(errno.ENOSPC, "No space left on device")

    def failing_open(self, mode="r", *a, **k):
        fh = real_open(self, mode, *a, **k)
        return HalfWriter(fh) if "x" in mode or "w" in mode else fh

    real_fdopen = os.fdopen

    def failing_fdopen(fd, mode="r", *a, **k):
        fh = real_fdopen(fd, mode, *a, **k)
        return HalfWriter(fh) if "w" in mode else fh

    # Whichever way the file is opened for writing, the write fails halfway.
    monkeypatch.setattr(Path, "open", failing_open)
    monkeypatch.setattr(os, "fdopen", failing_fdopen)
    out = OPS.precommit(mixed, where=mixed, write=True)
    monkeypatch.undo()
    assert out.exit == 1 and "No space" in out.reason
    assert sorted(p.name for p in mixed.iterdir() if "pre-commit" in p.name) == []
    assert OPS.precommit(mixed, where=mixed, write=True).exit == 0, "a retry succeeds"


def test_ddflows_own_command_is_not_among_the_programs_required(mixed):
    """`requires` excludes ddflow's command; the caller checks the one it chose."""
    reqs = PC.propose(mixed, ddflow_cmd="/opt/ddflow/bin/ddflow").requires
    assert not any("ddflow" in r for r in reqs), reqs


def test_hooks_without_their_own_stages_run_only_before_the_commit(mixed):
    """Bug B77eb4abf56: with no default_stages, pre-commit runs every hook that names no
    stages at EVERY installed hook type -- check-merge-conflict and gitleaks re-ran at
    commit-msg against the message file, and a '=======' line in a message was refused."""
    assert "\ndefault_stages: [pre-commit]\n" in PC.propose(mixed).text


@pytest.mark.skipif(shutil.which("pre-commit") is None, reason="pre-commit is not installed")
def test_pre_commit_itself_keeps_an_unstaged_hook_out_of_commit_msg(repo, tmp_path):
    """The same, asserted by pre-commit rather than by reading the text: a local hook that
    always fails and names no stages must not run at the commit-msg stage."""
    probe = PC.Hook(
        "always-fails", {"name": "always fails", "entry": "false", "language": "system"}
    )
    text = PC.render(
        PC.Proposal(stacks={}, repos=[PC.Repo("local", (probe,), "probe")], skipped=[])
    )
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(text)
    msg = tmp_path / "msg"
    msg.write_text("subject\n\n=======\n")
    r = subprocess.run(
        ["pre-commit", "run", "-c", str(cfg), "--hook-stage", "commit-msg",
         "--commit-msg-filename", str(msg)],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert r.returncode == 0 and "always fails" not in r.stdout, r.stdout + r.stderr


def test_a_temp_file_left_by_a_killed_run_does_not_block_the_write(mixed):
    """Bug B43c097b9b9: the temp name was derived from the pid, so a leftover from a killed run
    with the same pid (PID 1 in a container) was reported as the config appearing."""
    import os

    (mixed / f"..pre-commit-config.yaml.{os.getpid()}.tmp").write_text("stale")
    (mixed / f".pre-commit-config.yaml.{os.getpid()}.tmp").write_text("stale")
    out = OPS.precommit(mixed, where=mixed, write=True)
    assert out.exit == 0 and out.data["written"] is True, out.reason
    assert (mixed / ".pre-commit-config.yaml").read_text() == out.data["text"]


def test_a_ddflow_command_the_shell_cannot_split_is_refused(mixed):
    """Bug B1235a05ba2: pre-commit shlex-splits an entry, so an unbalanced quote
    would make every commit error; it was proposed (and written) with only a note."""
    out = OPS.precommit(mixed, where=mixed, ddflow_cmd='a "b', write=True)
    assert out.exit == REFUSED
    assert not (mixed / ".pre-commit-config.yaml").exists()


def test_the_payload_says_the_file_exists_once_it_was_written(mixed):
    """Bug B5f0c501ba4: `exists` was probed before the write and never updated."""
    out = OPS.precommit(mixed, where=mixed, write=True)
    assert out.data["written"] is True and out.data["exists"] is True


def test_every_run_with_a_config_in_place_says_how_to_activate_it(mixed):
    """Bug B287c338cf9: the hint appeared only on the run that wrote the file with
    pre-commit present; written first and pre-commit installed later, nothing ever said
    `pre-commit install`, and the config ran nothing."""
    from conftest import run_cli

    (mixed / ".pre-commit-config.yaml").write_text("repos: []\n")
    code, out, err = run_cli(mixed, "precommit")
    assert code == 0, err
    assert "pre-commit install" in out.split("exists --", 1)[1]


def test_the_activation_line_names_each_hook_type_whatever_the_file_on_disk_says(mixed):
    """Bug Bd6e6d40f1e: an operator's own config may lack default_install_hook_types, and then a
    plain `pre-commit install` sets up only the pre-commit hook -- check-msg never runs."""
    from conftest import run_cli

    (mixed / ".pre-commit-config.yaml").write_text("repos: []\n")
    code, out, err = run_cli(mixed, "precommit")
    assert code == 0, err
    assert "pre-commit install --hook-type pre-commit --hook-type commit-msg" in out


def test_a_tool_declared_only_in_a_nested_package_json_is_named_as_such(repo):
    """Bug B839afc3dbc: only the root package.json was read, so a monorepo was told its
    declared eslint did not exist. A root-level hook cannot reach a nested install, so
    none is proposed -- but the gap is said truthfully."""
    _commit(repo, {"web/package.json": json.dumps({"devDependencies": {"eslint": "^9"}})})
    p = PC.propose(repo)
    assert "eslint" not in _hooks(p)["local"]
    (note,) = [s for s in p.skipped if s.startswith("eslint")]
    assert "web/package.json" in note and "does not declare" not in note
