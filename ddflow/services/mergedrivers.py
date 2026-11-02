"""Merge drivers for the files every task touches (LK-baseline-merge-drivers).

Three kinds, one setup step with consent:

- UNION for sorted one-entry-per-line baselines (`[pins].baseline_globs`) and append-only
  logs (`[lease].append_only_globs`, written by `shared_files`): both sides' lines survive.
  `check_baselines` is the ratchet's verification of the union (sorted, unique, no markers).
- REGENERATE for pins and golden indexes: the existing `pinsregen` driver and post-merge hook
  of `pins_merge`, reused, never a second one.
- MERGIRAF, an opt-in evaluation for `*.py`: needs the binary and git 2.44; ddflow never
  installs it, it shows the exact lines and says unavailable when it cannot run.

`plan` shows the exact lines and writes nothing; `apply` writes them (the consent is the
caller's `--install`). `git merge-tree` honours these drivers, so the conflict probe sees what
the merge will do. Git is reached through `infra.git` only.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..core.admission import is_shared
from ..infra import git as G
from ..infra.fsio import replace_text
from . import pins_merge as PM
from . import shared_files as SF
from .pins import pin_files

MERGIRAF = "mergiraf"
#: The command git runs for the driver (mergiraf's documented setup; %P needs git 2.44).
MERGIRAF_DRIVER = "mergiraf merge --git %O %A %B -s %S -x %X -y %Y -p %P -l %L"
NEXT_CLI = "ddflow pins drivers --install"
NEXT_MCP = "ddflow_pins document=drivers install=true"
_MARK = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)


@dataclass
class Plan:
    """What the setup would add (``attrs`` lines, ``config`` git keys) and what is missing."""

    attrs: list[str] = field(default_factory=list)
    config: dict[str, str] = field(default_factory=dict)
    unavailable: list[str] = field(default_factory=list)
    regenerate: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        """The exact text, one line each, for the consent prompt."""
        out = [f".gitattributes: {a}" for a in self.attrs]
        out += [f"git config merge.{k}: {v}" for k, v in self.config.items()]
        out += [f"pins (existing driver): {f}" for f in self.regenerate]
        out += [f"unavailable: {u}" for u in self.unavailable]
        return out

    def empty(self) -> bool:
        return not (self.attrs or self.config)


def _have(repo: Path, line: str) -> bool:
    return SF._has_line(repo, line)


def _git_version(repo: Path) -> tuple[int, int] | None:
    r = G.run(repo, "--version")
    m = re.search(r"(\d+)\.(\d+)", r.out) if r.ok else None
    return (int(m[1]), int(m[2])) if m else None


def _min_git(cfg: Config) -> tuple[int, int]:
    m = re.match(r"(\d+)\.(\d+)", cfg.pins.mergiraf_min_git.strip())
    return (int(m[1]), int(m[2])) if m else (2, 44)


def keep_mergiraf(solved: int, total: int, min_pct: int) -> bool:
    """The evaluation's verdict: of ``total`` textual conflicts replayed, ``solved`` were
    resolved; keep it only when that is MORE than ``min_pct`` percent. No conflicts: drop."""
    return total > 0 and solved * 100 > min_pct * total


def _mergiraf_plan(repo: Path, cfg: Config, plan: Plan) -> None:
    md = cfg.pins
    have = _git_version(repo)
    need = _min_git(cfg)
    if shutil.which(MERGIRAF) is None:
        plan.unavailable.append(
            f"the `{MERGIRAF}` binary is not on PATH; ddflow installs nothing, install it yourself, then `{NEXT_CLI}` (MCP: {NEXT_MCP})"
        )
    if have is None or have < need:
        got = "unknown" if have is None else f"{have[0]}.{have[1]}"
        plan.unavailable.append(
            f"git {need[0]}.{need[1]} or later is needed for the recommended setup, found {got}; upgrade git, then `{NEXT_CLI}` (MCP: {NEXT_MCP})"
        )
    if plan.unavailable:
        return
    plan.config["mergiraf.name"] = "mergiraf"
    plan.config["mergiraf.driver"] = MERGIRAF_DRIVER
    for g in md.mergiraf_globs:
        if not _have(repo, f"{g} merge={MERGIRAF}"):
            plan.attrs.append(f"{g} merge={MERGIRAF}")


def plan(repo: Path, cfg: Config) -> Plan:
    """The lines the setup would add; nothing is written."""
    p = Plan()
    for g in cfg.pins.baseline_globs:
        line = SF.union_line(g)
        if not any(ch.isspace() for ch in g) and not _have(repo, line):
            p.attrs.append(line)
    if cfg.pins.after_merge != "off":
        p.regenerate = [PM._attr_line(repo, f) for f in pin_files(repo, cfg)]
    if cfg.pins.mergiraf:
        _mergiraf_plan(repo, cfg, p)
    return p


def apply(repo: Path, cfg: Config) -> list[str]:
    """Write what `plan` shows (the caller has the consent); the actions taken."""
    p = plan(repo, cfg)
    acts: list[str] = []
    attrs = Path(repo) / ".gitattributes"
    if p.attrs:
        text = attrs.read_text("utf-8") if attrs.exists() else ""
        tail = "" if not text or text.endswith("\n") else "\n"
        replace_text(attrs, text + tail + "\n".join(p.attrs) + "\n")
        acts += [f"added '{a}' to .gitattributes" for a in p.attrs]
    for key, val in p.config.items():
        if G.run(repo, "config", "--local", "--get", f"merge.{key}").out.strip() != val:
            r = G.run(repo, "config", "--local", f"merge.{key}", val)
            acts.append(
                f"set git config merge.{key}"
                if r.ok
                else f"could not set git config merge.{key}: {r.err or 'git failed'}"
            )
    acts += [f"added '{x}' to .gitattributes" for x in SF.sync_attributes(repo)]
    acts += PM.install(repo, cfg)
    return acts


def check_baselines(repo: Path, cfg: Config) -> list[str]:
    """The ratchet's check of the union: each tracked baseline file that is not sorted, has a
    repeated entry or holds a conflict marker, as a sentence naming the remedy."""
    tracked = G.git_paths(repo, "ls-files") or []
    bad: list[str] = []
    for path in tracked:
        if not is_shared(path, cfg.pins.baseline_globs):
            continue
        try:
            text = (Path(repo) / path).read_text("utf-8")
        except OSError as exc:
            bad.append(
                f"{path} could not be read ({exc.strerror}): the check is unavailable for it"
            )
            continue
        rows = [r for r in text.splitlines() if r.strip() and not r.startswith("#")]
        if _MARK.search(text):
            bad.append(
                f"{path} has conflict markers: resolve them by hand, keep both sides' entries,"
                f" `sort -u -o {path} {path}`, then `ddflow pins drivers --check`"
            )
            continue
        why = (
            "is not sorted"
            if rows != sorted(rows)
            else "repeats an entry"
            if len(rows) != len(set(rows))
            else ""
        )
        if why:
            bad.append(
                f"{path} {why} after the union merge: `sort -u -o {path} {path}`, commit, then"
                " `ddflow pins drivers --check`"
            )
    return bad
