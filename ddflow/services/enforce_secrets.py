"""The commit check that refuses an event shard line holding a credential.

Redaction at write time is the control; this is the backstop for a line that got past it
(an older writer, a hand edit, a merge of another clone's shard). It reads the lines the
commit ADDS to a shard under the event directory, through `core.secret_shapes` (the same
table the redactor applies -- no second scanner) and says WHERE: the shard and the line
number, and the shape's name. It never prints the value.

Lines already committed are not read again: scrubbing history is a separate decision, and
a shard that carries an old leak must still be committable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from ..config import Config
from ..core.outcome import FAIL, OK, Verdict
from ..core.secret_shapes import shape_hit
from ..infra import git as G
from ..infra import proc as P
from ..infra.log import EventLog
from .enforce import _index_tree

_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,\d+)? @@")


def _leaves(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(k)
            yield from _leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v)


def line_hit(line: str) -> str:
    """The shape name a shard line matches (raw, or once its JSON escapes are decoded), or ""."""
    hit = shape_hit(line)
    if hit:
        return hit
    try:
        doc = json.loads(line)
    except ValueError:
        return ""
    for leaf in _leaves(doc):
        hit = shape_hit(leaf)
        if hit:
            return hit
    return ""


def added_hits(diff: str) -> list[tuple[str, int, str]]:
    """(path, line number, shape) for every added line of a zero-context diff that hits."""
    out: list[tuple[str, int, str]] = []
    path, lineno = "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].removeprefix("b/")
        elif raw.startswith("@@"):
            m = _HUNK.match(raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and lineno:
            hit = line_hit(raw[1:])
            if hit:
                out.append((path, lineno, hit))
            lineno += 1
    return out


def check_event_secrets(repo: Path, cfg: Config | None = None) -> Verdict:
    """(exit, message): refuse (or warn, `[enforce].event_secrets`) a staged event shard line
    that matches a credential shape. A diff git cannot produce is not a clean one."""
    cfg = cfg or Config.load(repo)
    mode = cfg.enforce.event_secrets
    if mode == "off":
        return Verdict(OK, "")
    tree = _index_tree(repo)
    try:
        rel = EventLog(repo, log_cfg=cfg.log).dir.resolve().relative_to(Path(repo).resolve())
    except ValueError:
        rel = Path(".ddflow/events")
    r = G.run(
        tree,
        "diff",
        "--cached",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        "-U0",
        "--diff-filter=ACMR",
        "--",
        str(rel),
        timeout=P.TIMEOUTS["git_listing"],
    )
    if not r.ok:
        lines = ["ddflow: could not read the staged event shards to scan them for credentials."]
        hits: list[tuple[str, int, str]] = []
    else:
        hits = added_hits(r.out)
        lines = []
    if hits:
        lines = [
            "ddflow: this commit adds event shard line(s) that look like a credential:",
            *(f"  {p}:{n}  ({shape})" for p, n, shape in hits[:20]),
            "",
            "The value is not shown. Unstage that shard, remove the line, and file a bug: "
            "write-time redaction should have caught it.",
        ]
    if not lines:
        return Verdict(OK, "")
    msg = "\n".join([*lines, "", f'Policy is [enforce].event_secrets = "{mode}".'])
    if mode == "warn":
        return Verdict(OK, msg + '\n\n(warning only; set the policy to "block" to refuse)')
    return Verdict(FAIL, msg)
