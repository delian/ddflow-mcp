"""pre-commit as a companion: a `.pre-commit-config.yaml` proposed for THIS project.

The pre-commit framework (pre-commit.com) runs a project's linters, formatters and
secret scanners before a commit exists, instead of only in CI afterwards. ddflow does not
ship one configuration for everybody: it reads which stacks the repository actually has
and proposes the hooks that fit them, pinned, with ddflow's own checks as `repo: local`
hooks -- so the framework owns `.git/hooks/` alone and ddflow's lease and commit-message
checks still run inside it (research Rb5e33fdbf9: installed side by side, one of the two
ends up not running or running as a renamed legacy hook).

It PROPOSES. Nothing here installs anything, and a file is written only on request and
never over an existing one: which checks gate someone's commits is their decision.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..infra import worktree as W

#: Release tags current on 2026-09-29 (`git ls-remote --tags`). Pins age by design --
#: an unpinned hook changes under you -- and `pre-commit autoupdate` moves them forward.
PINS = {
    "https://github.com/pre-commit/pre-commit-hooks": "v6.0.0",
    "https://github.com/astral-sh/ruff-pre-commit": "v0.16.9",
    "https://github.com/gitleaks/gitleaks": "v8.30.1",
    "https://github.com/shellcheck-py/shellcheck-py": "v0.11.0.1",
    "https://github.com/hadolint/hadolint": "v2.15.1",
}

#: What marks a stack as present, by path.
_STACKS: dict[str, re.Pattern[str]] = {
    "python": re.compile(
        r"(?:^|/)(?:pyproject\.toml|setup\.py|setup\.cfg|requirements[^/]*\.txt)$|\.py$"
    ),
    "javascript": re.compile(r"(?:^|/)package\.json$"),
    "docker": re.compile(r"(?:^|/)(?:Dockerfile|Containerfile)(?:\.[^/]*)?$|\.Dockerfile$"),
    "shell": re.compile(r"\.(?:sh|bash)$"),
    "go": re.compile(r"(?:^|/)go\.mod$"),
    "rust": re.compile(r"(?:^|/)Cargo\.toml$"),
}


@dataclass(frozen=True)
class Hook:
    id: str
    fields: dict = field(default_factory=dict)  #: name, entry, language, args, stages...


@dataclass(frozen=True)
class Repo:
    url: str  #: a git URL, or "local"
    hooks: tuple[Hook, ...]
    why: str

    @property
    def rev(self) -> str:
        return PINS.get(self.url, "")


@dataclass
class Proposal:
    stacks: dict[str, list[str]]  #: stack -> the paths that showed it (at most three)
    repos: list[Repo]
    skipped: list[str]  #: what was NOT proposed, and why -- so a gap is not silent
    text: str = ""


#: How many paths to cite as evidence for a stack.
_EVIDENCE = 3


def detect_stacks(paths: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for p in paths:
        for stack, pat in _STACKS.items():
            if pat.search(p):
                found.setdefault(stack, [])
                if len(found[stack]) < _EVIDENCE:
                    found[stack].append(p)
    return found


def _has(paths: list[str], *suffixes: str) -> bool:
    return any(p.endswith(suffixes) for p in paths)


def _local(hook_id: str, name: str, entry: str, **extra) -> Hook:
    return Hook(hook_id, {"name": name, "entry": entry, "language": "system", **extra})


def _package_json_declares(root: Path, tool: str) -> bool:
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    return tool in deps


def propose(root: Path, *, ddflow_cmd: str = "ddflow") -> Proposal | None:
    """The hooks that fit ``root``'s stacks. None when git cannot list its files."""
    paths = W.git_paths(root, "ls-files", "--cached", "--others", "--exclude-standard")
    if paths is None:
        return None
    stacks = detect_stacks(paths)
    skipped: list[str] = []
    hygiene = [
        Hook("trailing-whitespace"),
        Hook("end-of-file-fixer"),
        Hook("check-merge-conflict"),
        Hook("check-added-large-files"),
        Hook("detect-private-key"),
    ]
    hygiene += [Hook(h) for h, sfx in (("check-yaml", (".yaml", ".yml")), ("check-toml", (".toml",)), ("check-json", (".json",))) if _has(paths, *sfx)]  # fmt: skip
    repos = [
        Repo("https://github.com/pre-commit/pre-commit-hooks", tuple(hygiene),
             "whitespace, merge markers, oversized files, private keys, config files that do not parse"),
        Repo("https://github.com/gitleaks/gitleaks", (Hook("gitleaks"),),
             "secrets in the diff (reads .gitleaks.toml when the project has one)"),
    ]  # fmt: skip
    if "python" in stacks:
        repos.append(Repo("https://github.com/astral-sh/ruff-pre-commit",
                          (Hook("ruff-check"), Hook("ruff-format")),
                          "Python lint and format, with the project's own ruff configuration"))  # fmt: skip
    if "shell" in stacks:
        repos.append(Repo("https://github.com/shellcheck-py/shellcheck-py",
                          (Hook("shellcheck"),), "shell scripts"))  # fmt: skip
    if "docker" in stacks:
        repos.append(Repo("https://github.com/hadolint/hadolint", (Hook("hadolint-docker"),),
                          "Dockerfiles (runs hadolint's image; needs docker)"))  # fmt: skip
    local: list[Hook] = []
    if "javascript" in stacks:
        for tool, entry in (("eslint", "npx --no-install eslint"), ("prettier", "npx --no-install prettier --check")):  # fmt: skip
            if _package_json_declares(root, tool):
                local.append(_local(tool, tool, entry, files=r"\.(?:[cm]?[jt]sx?)$"))
            else:
                skipped.append(
                    f"{tool}: package.json does not declare it, so there is nothing to run"
                )
    if "go" in stacks:
        local += [
            _local("gofmt", "gofmt", "gofmt -l -d", files=r"\.go$"),
            _local("go-vet", "go vet", "go vet ./...", pass_filenames=False, types=["go"]),
        ]
    if "rust" in stacks:
        local += [
            _local("cargo-fmt", "cargo fmt", "cargo fmt --check", pass_filenames=False, types=["rust"]),
            _local("cargo-clippy", "cargo clippy", "cargo clippy -- -D warnings", pass_filenames=False, types=["rust"]),
        ]  # fmt: skip
    # ddflow's own checks, INSIDE the framework rather than beside it.
    local += [
        _local("ddflow-check-commit", "ddflow: claim before you edit; generated views current",
               f"{ddflow_cmd} hooks check-commit", pass_filenames=False, always_run=True,
               stages=["pre-commit"]),
        _local("ddflow-check-msg", "ddflow: the commit message carries what the project requires",
               f"{ddflow_cmd} hooks check-msg", stages=["commit-msg"]),
    ]  # fmt: skip
    repos.append(
        Repo("local", tuple(local), "tools the project already has, and ddflow's own checks")
    )
    out = Proposal(stacks=stacks, repos=repos, skipped=skipped)
    out.text = render(out)
    return out


def _yaml(value) -> str:
    """A scalar or flat list as YAML. JSON strings are valid YAML scalars, which is all
    the quoting this file needs."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_yaml(v) for v in value) + "]"
    return json.dumps(value)


def render(p: Proposal) -> str:
    stacks = ", ".join(sorted(p.stacks)) or "none detected"
    lines = [
        "# Proposed by `ddflow precommit` for this repository's stacks: " + stacks + ".",
        "# Review it, then `pre-commit install` (installs the pre-commit and commit-msg hooks).",
        "# `pre-commit autoupdate` moves the pinned revisions forward.",
        "default_install_hook_types: [pre-commit, commit-msg]",
        "repos:",
    ]
    for repo in p.repos:
        lines.append(f"  # {repo.why}")
        lines.append(f"  - repo: {repo.url}")
        if repo.url != "local":
            lines.append(f"    rev: {repo.rev}")
        lines.append("    hooks:")
        for hook in repo.hooks:
            lines.append(f"      - id: {hook.id}")
            for key, val in hook.fields.items():
                lines.append(f"        {key}: {_yaml(val)}")
    for s in p.skipped:
        lines.append(f"# not proposed -- {s}")
    return "\n".join(lines) + "\n"


def config_path(root: Path) -> Path:
    return Path(root) / ".pre-commit-config.yaml"


def stacks_summary(p: Proposal) -> str:
    return "; ".join(
        f"{s} ({', '.join(PurePosixPath(x).name for x in ev)})"
        for s, ev in sorted(p.stacks.items())
    )
