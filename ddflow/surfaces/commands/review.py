"""`ddflow review` and `ddflow reviewers ...` — the human surface for `api.review`.

The CLI passes `print` as the progress callback, so a review that takes two minutes says
what it is doing while it does it. The MCP tool collects the same lines and returns them
as the body, which is what its PROSE_TOOLS entry has always promised.

`reviewers presets`, `add` and `test` stay here: they are configuration authoring and a
live probe, neither of which has an MCP tool, and `test` deliberately runs a canned diff
rather than the project's.
"""

from __future__ import annotations

import json
import sys

# The SUBMODULE. `from ...api import review` would bind the re-exported function; this
# form reads `ddflow.api.review` out of sys.modules and cannot be shadowed.
import ddflow.api.review as A

from ..context import FAIL, NOTHING, OK, Ctx


def cmd_review(a, c: Ctx) -> int:
    out = A.review(
        c.repo,
        gate=a.gate,
        item=a.id or "",
        intent=a.intent or "",
        context=a.context or "",
        base=a.base or "",
        commit=a.commit or "",
        branch=a.branch or "",
        called_from=c.called_from,
        # Streamed as it happens. Silence for two minutes reads as a hang, and an agent
        # watching a hung tool kills it.
        on_progress=lambda line: print(line, flush=True),
        agent=c.requested_agent,
    )
    if out.exit == FAIL or (out.exit == NOTHING and not out.data.get("reviewer")):
        print(out.reason, file=sys.stderr)
    return out.exit


def _reviewers_detect(a, c: Ctx) -> int:
    out = A.reviewers_detect(c.repo, write=a.write, agent=c.requested_agent)
    if out.exit == NOTHING:
        print(out.reason, file=sys.stderr)
        return NOTHING
    print(out.data["text"])
    return OK


def _reviewers_list(a, c: Ctx) -> int:
    out = A.reviewers_list(c.repo, agent=c.requested_agent)
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    print(out.data["text"])
    return OK


def _reviewers_presets(_a, _c: Ctx) -> int:
    from ...services import review as R

    for name, spec in sorted(R.PRESETS.items()):
        where = spec.get("base_url") or spec.get("command", "")
        print(f"  {name:<12} {spec['kind']:<9} {where}")
    print("\nAdd one with:  ddflow reviewers add --preset <name> [--model M]")
    return OK


def _reviewers_add(a, c: Ctx) -> int:
    """Append a `[[reviewer]]` block. Writes the KEY's variable NAME, never the key."""
    from ...config import csv_list
    from ...services import review as R

    preset = dict(R.PRESETS.get(a.preset, {}))
    if a.preset and not preset:
        print(f"unknown preset {a.preset!r}; `ddflow reviewers presets`", file=sys.stderr)
        return FAIL
    if a.model:
        preset["model"] = a.model
    if a.base_url:
        preset["base_url"] = a.base_url
    if a.gates:
        preset["gates"] = csv_list(a.gates)
    name = a.name or a.preset or preset.get("model", "reviewer")
    preset.setdefault("family", R.family_of(preset.get("model", "")))
    if a.no_launch:
        preset.pop("launch", None)
    body = [f'\n[[reviewer]]\nname = "{name}"']
    launch = preset.pop("launch", None)
    for k, v in preset.items():
        body.append(f"{k} = {json.dumps(v)}")
    if launch:
        body.append("launch = " + json.dumps(launch))
    path = c.repo / ".ddflow" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = path.read_text("utf-8") if path.exists() else ""
    path.write_text(prev.rstrip() + "\n" + "\n".join(body) + "\n", "utf-8")
    note = ""
    if preset.get("api_key_env"):
        note = (
            f"\n  Set ${preset['api_key_env']} in your environment. The KEY is never "
            f"written to the config — only the variable's name, because this file is "
            f"committed."
        )
    c.out(f"added reviewer {name!r} to {path}{note}", {"name": name})
    return OK


def _reviewers_test(a, c: Ctx) -> int:
    """A live probe against a CANNED diff.

    Deliberately not the project's diff: this answers "does this endpoint answer at all",
    and running it over real work would make a connectivity check look like a review.
    """
    from ...services import review as R

    targets = [r for r in R.load_reviewers(c.repo) if not a.name or r.name == a.name]
    if not targets:
        print(f"no reviewer named {a.name!r}; `ddflow reviewers list`", file=sys.stderr)
        return FAIL
    worst = OK
    for r in targets:
        res = R.review(
            r,
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            "@@ -1,3 +1,3 @@\n def f(items):\n-    return sum(items) / len(items)\n"
            "+    return sum(items) / len(items) if items else 0\n",
            intent="guard the empty-list case in the mean helper",
        )
        print(
            f"  {r.name}: {res.label} ({res.coverage()}, {res.elapsed_s:.1f}s)"
            + (f" — {res.reason}" if res.reason else "")
        )
        for f in res.findings[:3]:
            print(f"      [{f.severity}] {f.title[:90]}")
        worst = max(worst, 0 if res.status == R.REVIEWED else res.status)
    return worst


def cmd_reviewers(a, c: Ctx) -> int:
    """A dispatcher. Each subcommand is its own function — they share nothing but the
    config file they read, and the chain of `if` arms was 18 branches."""
    return {
        "detect": _reviewers_detect,
        "list": _reviewers_list,
        "presets": _reviewers_presets,
        "add": _reviewers_add,
        "test": _reviewers_test,
    }.get(a.reviewers_cmd or "list", _reviewers_list)(a, c)
