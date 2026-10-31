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


def added_hits(diff: str) -> list[tuple[str, int, str, str]]:
    """(path, line number, shape, line) for every added line of a zero-context diff that
    hits. The line is for comparison inside this module and is never printed."""
    out: list[tuple[str, int, str, str]] = []
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
                out.append((path, lineno, hit, raw[1:]))
            lineno += 1
    return out


def _diff(tree: Path, rel: Path, against: str) -> str | None:
    """The zero-context diff of the index against ``against`` for the event directory."""
    r = G.run(
        tree,
        "diff",
        "--cached",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        "-U0",
        "--diff-filter=ACMR",
        against,
        "--",
        str(rel),
        timeout=P.TIMEOUTS["git_listing"],
    )
    return r.out if r.ok else None


def _new_hits(tree: Path, rel: Path) -> list[tuple[str, int, str]] | None:
    """Hits on lines this commit adds; None when git cannot say.

    In a merge a line the other parent already has is not this commit's to add (merging
    `main` into a branch must not be refused for a leak `main` already committed), so only
    a line new against BOTH parents counts."""
    diff = _diff(tree, rel, "HEAD")
    if diff is None:
        return None
    hits = added_hits(diff)
    other = G.run(
        tree, "rev-parse", "-q", "--verify", "MERGE_HEAD", timeout=P.TIMEOUTS["git_listing"]
    )
    if hits and other.ok and other.out.strip():
        theirs = _diff(tree, rel, other.out.strip())
        if theirs is None:
            return None
        known = {(p, text) for p, _n, _s, text in added_hits(theirs)}
        hits = [h for h in hits if (h[0], h[3]) in known]
    return [(p, n, shape) for p, n, shape, _t in hits]


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
    hits = _new_hits(tree, rel)
    lines: list[str] = []
    if hits is None:
        lines = ["ddflow: could not read the staged event shards to scan them for credentials."]
    elif hits:
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
