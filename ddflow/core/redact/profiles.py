"""Named redaction profiles, the `Redactor` that applies them, and `redact_argv`.

Profiles: `log` (the committed event log: new writes only), `view` (committed views),
`export` (exported documents) and `upstream` (anything leaving the machine in a report);
the shape of a command line is structural, `redact_argv`, not a profile.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .engine import redact_text

if TYPE_CHECKING:
    from .engine import Redacted

_PROGRAMS = frozenset({"ddflow", "ddflow-mcp", "python", "python3", "uv", "uvx"})
_WORD = re.compile(r"[a-z][a-z0-9_-]{0,23}")


@dataclass(frozen=True)
class Profile:
    """Which machine-local inputs a profile removes. `None` means "the machine's own",
    which `services.redact_report.redactor` resolves from the environment; `""` means
    "none", so the output is byte-identical on every machine."""

    name: str
    hostname: str | None
    home: str | None
    repo_root: str | None
    secrets: str = "marker"
    #: How the machine's own home path is rendered: "marker" replaces it with the opaque
    #: `[REDACTED:path]`; "resolvable" replaces it with `$HOME`, keeping the tail, so a path
    #: an operator must recover stays addressable (bug B-redaction-hides-parked-work-path).
    home_style: str = "marker"


#: `repo_root` is empty where the repository's own directory name is the project's and stays.
PROFILES: Mapping[str, Profile] = {
    p.name: p
    for p in (
        # The committed event log: the machine's own hostname and $HOME go as well, and
        # secrets keep their context.
        Profile("log", None, None, "", secrets="mask", home_style="resolvable"),
        # A committed view: byte-identical on every machine (roborev on 9b6db6bc).
        Profile("view", "", "", ""),
        # An exported document: the rendering machine's hostname and $HOME also go.
        Profile("export", None, None, ""),
        # A report that leaves the machine: everything of the machine's own goes.
        Profile("upstream", None, None, None),
    )
}
#: `argv` is structural (an allowlist, `redact_argv`), not a text profile: no Redactor.


class Redactor:
    """`Redactor("view", secret_patterns=...).text(s)`; `services.redact_report.redactor`
    builds one from the config."""

    def __init__(
        self,
        profile: str,
        *,
        secret_patterns: Iterable[str],
        names: Iterable[str] = (),
        hostname: str | None = None,
        home: str | None = None,
        repo_root: str | None = None,
    ) -> None:
        if profile not in PROFILES:
            raise ValueError(f"unknown redaction profile {profile!r}; one of {', '.join(PROFILES)}")
        base = PROFILES[profile]
        self.profile = base
        self.secret_patterns = list(secret_patterns)
        self.names = list(names)
        self.hostname = base.hostname if hostname is None else hostname
        self.home = base.home if home is None else home
        self.repo_root = base.repo_root if repo_root is None else repo_root
        self.home_style = base.home_style

    def text(self, text: object) -> Redacted:
        return redact_text(
            text,
            secret_patterns=self.secret_patterns,
            hostname=self.hostname,
            names=self.names,
            home=self.home,
            repo_root=self.repo_root,
            secret_style=self.profile.secrets,
            home_style=self.home_style,
        )


def redact_argv(
    argv: Sequence[str],
    subcommands: Iterable[str] | None = None,
    flags: Iterable[str] | None = None,
) -> list[str]:
    """The shape of a command line, from allowlists and nothing else.

    Kept: the program (when it is ddflow or a Python launcher), the leading words the
    caller lists in `subcommands` (the CLI's own verbs), and the flag names the caller
    lists in `flags` (the CLI's own flags, matched exactly, with or without `=value`).
    Everything else is a placeholder: `<flag>` for any other dash-led token, `<value>`
    for a word right after a kept flag, `<arg>` for any other word. Nothing is inferred
    from how a token looks -- a dash-led secret (`-hunter2`, `-psecret`, `--s3cr3t`) cannot
    be told from a flag, so it is only ever kept if the caller named it as one."""
    allowed = set(subcommands) if subcommands is not None else set()
    known = {"-m", *(flags or ())}  # `-m` is the Python launcher's own
    out: list[str] = []
    leading = True
    for i, raw in enumerate(argv):
        tok = str(raw)
        if i == 0:
            base = tok.replace("\\", "/").rsplit("/", 1)[-1]
            out.append(base if base in _PROGRAMS else "<program>")
        elif tok in _PROGRAMS and out[-1] == "-m":
            out.append(tok)  # `python -m ddflow`
        elif tok.startswith("-") and tok != "-":
            leading = False
            name, eq, _value = tok.partition("=")
            shown = name if name in known else "<flag>"
            out.append(f"{shown}=<value>" if eq else shown)
        elif leading and _WORD.fullmatch(tok) and tok in allowed:
            out.append(tok)
        else:
            leading = False
            after_flag = out[-1] in known and "=" not in out[-1]
            out.append("<value>" if after_flag else "<arg>")
    return out
