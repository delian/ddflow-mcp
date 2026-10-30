"""Where a reviewer's (or a lane's) API key comes from: the environment, then a key file.

One resolver for every kind (openai, anthropic, gemini) and for the lane endpoint, so the
rules live once (r-reuse-core). The environment variable wins; the file is the fallback
that lets every agent and run find a key without exporting it first.

The rules, ssh-style, because this handles a secret:

* the file must be a regular file OWNED by the current user and not readable or writable
  by group or others (mode & 0o077 == 0); anything else is refused with the reason;
* a symlink is followed and the TARGET is held to the same test;
* the test runs on the opened descriptor (`fstat`), so the file cannot change between the
  check and the read;
* it is read at call time and never cached;
* the VALUE is returned only in `KeyResult.value`, which `repr` hides; every reason text
  names the path and never the content. `scrub` removes a value from text a server echoed.

The path is data; the value never is.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

#: A safety bound on what is read from a key file (a real key is under a kilobyte); not a
#: tunable: a file larger than this is not a key file.
MAX_KEY_FILE_BYTES = 16384


class KeyResult:
    """The key (``value``) or why there is none (``reason``). ``source`` is ``env`` or ``file``.

    Deliberately NOT a dataclass: `dataclasses.asdict` and the generated `__eq__` and
    `__repr__` would reach the value. `repr`, `str` and pickling name the source alone;
    `vars` fails (no `__dict__`) and `__getstate__` refuses; `.value` is the one way in:
    pass it to the request and nowhere else."""

    __slots__ = ("_value", "reason", "source")

    def __init__(self, value: str = "", reason: str = "", source: str = "") -> None:
        self._value, self.reason, self.source = value, reason, source

    @property
    def value(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return f"KeyResult(source={self.source!r}, reason={self.reason!r}, value=<hidden>)"

    def __reduce__(self):
        raise TypeError("a KeyResult holds a secret and is not serialised")

    def __getstate__(self):
        raise TypeError("a KeyResult holds a secret and is not serialised")


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path))


def _refusal(path: Path, st: os.stat_result) -> str:
    """Why ``st`` (the opened file's stat) is not an acceptable key file, or ``""``."""
    if not stat.S_ISREG(st.st_mode):
        return f"key file {path} is not a regular file"
    if st.st_uid != os.geteuid():
        return f"key file {path} is not owned by the current user; refusing to read it"
    if st.st_mode & 0o077:
        return (
            f"key file {path} is accessible by group or others "
            f"(mode {stat.S_IMODE(st.st_mode):04o}); refusing to read it: chmod 600 {path}"
        )
    return ""


def read_key_file(path: str) -> KeyResult:
    """The key in ``path``, or the reason it cannot be used. Never raises."""
    p = _expand(path)
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return KeyResult(reason=f"key file {p} does not exist")
    except ValueError:  # an embedded NUL in the path
        return KeyResult(reason="key file path is not a valid path")
    except OSError as exc:
        return KeyResult(reason=f"key file {p} cannot be opened ({exc.strerror or 'error'})")
    try:
        why = _refusal(p, os.fstat(fd))
        if why:
            return KeyResult(reason=why)
        data = os.read(fd, MAX_KEY_FILE_BYTES + 1)
    except OSError as exc:
        return KeyResult(reason=f"key file {p} cannot be read ({exc.strerror or 'error'})")
    finally:
        os.close(fd)
    if len(data) > MAX_KEY_FILE_BYTES:
        return KeyResult(reason=f"key file {p} is too large to be a key")
    try:
        text = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return KeyResult(reason=f"key file {p} is not valid UTF-8 text")
    if not text:
        return KeyResult(reason=f"key file {p} is empty")
    if not text.isprintable() or any(c.isspace() for c in text):
        return KeyResult(
            reason=f"key file {p} is not a single printable word; expected the key alone"
        )
    return KeyResult(value=text, source="file")


def resolve_key(env_name: str, key_file: str = "", default_env: str = "") -> KeyResult:
    """The environment variable ``env_name`` first, then ``key_file``. ``reason`` names what
    to set when neither gives a key (names and paths only), and is ``""`` when ``env_name``
    and ``key_file`` are both unset (a keyless endpoint) unless ``default_env`` is given."""
    name = env_name
    if name and (val := os.environ.get(name, "")):
        if not val.isprintable() or any(c.isspace() for c in val):
            return KeyResult(reason=f"${name} holds a control or whitespace character; not a key")
        return KeyResult(value=val, source="env")
    if key_file:
        got = read_key_file(key_file)
        if got:
            return got
        return KeyResult(
            reason=f"no API key: ${name} is not set and {got.reason}" if name else got.reason
        )
    if default_env:
        return KeyResult(reason=f"no API key: set ${env_name or default_env} in the environment")
    return KeyResult()


def scrub(text: str, key: str) -> str:
    """``text`` with ``key`` replaced, plain or URL-quoted (any hex case); a server may echo it."""
    if not key:
        return text
    import re
    from urllib.parse import quote

    for form in {key, quote(key), quote(key, safe="")}:
        text = re.sub(
            re.escape(form), "[REDACTED:key]", text, flags=re.IGNORECASE if "%" in form else 0
        )
    return text


def status(env_name: str, key_file: str = "") -> str:
    """One line for a listing: names and paths only, never a value. Decided by `resolve_key`,
    so a listing and a review cannot disagree."""
    got = resolve_key(env_name, key_file)
    if got:
        return f"env ${env_name}" if got.source == "env" else f"file {key_file}"
    if got.reason:
        return f"UNAVAILABLE: {got.reason}"
    if not env_name:
        return "keyless"
    return (
        f'missing: set ${env_name} or add api_key_file = "~/path/to/key" (mode 600) '
        f"to the local reviewers file"
    )


def _tracked(root: Path, path: Path) -> bool:
    """Whether git tracks ``path`` (a force-added "local" file is shared, not local)."""
    import subprocess  # nosec B404 - fixed argv, no shell

    try:
        done = subprocess.run(  # nosec B603 B607
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", str(path)],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def refuse_shared_key_files(root: Path, paths: tuple[Path, ...]) -> None:
    """Raise ValueError when a reviewers file that is NOT machine-local sets ``api_key_file``.

    A committed or shared config reaches every clone; a key path there points at another
    person's home and invites committing the secret's location. Only the git-ignored
    `.ddflow/local/` files may name one. Names the file and the reviewer, never a value."""
    from ..config_sections._layers import LOCAL_DIR, read_toml

    local = (Path(root) / LOCAL_DIR).resolve()
    for path in paths:
        p = Path(path)
        if not p.is_file():
            continue
        if p.resolve().parent == local and not _tracked(root, p):
            continue
        for n, raw in enumerate(read_toml(p).get("reviewer") or [], 1):
            if isinstance(raw, dict) and "api_key_file" in raw:
                who = raw.get("name") or raw.get("model") or f"#{n}"
                raise ValueError(
                    f"[[reviewer]] {who} in {p}: api_key_file is accepted only in the "
                    f"machine-local .ddflow/local/reviewers.toml, never in a committed or "
                    f"shared config"
                )


def key_hints(reviewers: list[object]) -> list[str]:
    """One line per enabled reviewer with neither its variable nor a readable key file:
    names and paths only. ``reviewers`` are `Reviewer` objects."""
    out = []
    for r in reviewers:
        if not getattr(r, "enabled", True) or getattr(r, "kind", "") == "command":
            continue
        st = status(getattr(r, "api_key_env", ""), getattr(r, "api_key_file", ""))
        if st.startswith(("missing", "UNAVAILABLE")):
            out.append(f"reviewer {r.name}: {st}")  # type: ignore[attr-defined]
    return out
