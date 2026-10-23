"""Run the tool-diet accuracy eval against one OpenAI-compatible model (a configured reviewer).

    uv run python tests/eval/tool_diet/td_run.py REVIEWER [--surfaces flat,families] [--repeat N]

Hand-run, never part of the suite: it spends model tokens. It prints a JSON report (bytes the
model is shown, per-surface tallies, every miss) for the research note. Each scenario is asked
`repeat` times on each surface.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from td_grade import grade, tally
from td_scenarios import SCENARIOS
from td_surfaces import SURFACES, size, tools_for

SYSTEM = (
    "You operate ddflow, a work queue for software projects, through its tools. Answer the "
    "request with exactly one tool call and no other text."
)


def ask(
    rev, tools: list[dict], prompt: str, timeout: float, after: list[dict] | None = None
) -> tuple[str, str, int]:
    """``(tool name, arguments text, prompt tokens)`` of the model's first tool call.

    ``after``: messages that follow the request (a first call and the refusal it got), for the
    one retry a real agent gets."""
    payload = {
        "model": rev.model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
            *(after or []),
        ],
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": 8000,
        **rev.extra_body,
    }
    headers = {"Content-Type": "application/json"}
    if rev.api_key():
        headers["Authorization"] = f"Bearer {rev.api_key()}"
    req = urllib.request.Request(
        rev.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    msg = body["choices"][0]["message"]
    used = (body.get("usage") or {}).get("prompt_tokens", 0)
    calls = msg.get("tool_calls") or []
    if not calls:
        return "", "", used
    fn = calls[0]["function"]
    return fn.get("name", ""), fn.get("arguments", "{}"), used


def retry(rev, tools, sc, surface, name, raw, timeout, first):
    """Tell the model what the server said and let it call once more; keep the better call."""
    from td_surfaces import resolve

    try:
        _m, _a, error = resolve(surface, name, json.loads(raw))
    except ValueError:
        error = "arguments were not valid JSON"
    after = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": name, "arguments": raw},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": error},
    ]
    try:
        name2, raw2, used2 = ask(rev, tools, sc.prompt, timeout, after)
    except Exception:
        return first, name, raw, 0
    return grade(sc, surface, name2, raw2), name2, raw2, used2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reviewer")
    ap.add_argument("--surfaces", default=",".join(SURFACES))
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--retry", action="store_true", help="give a refused call one retry")
    args = ap.parse_args(argv)

    from ddflow.services.review import load_reviewers

    revs = {r.name: r for r in load_reviewers(Path(args.repo).resolve())}
    if args.reviewer not in revs:
        print(f"no reviewer {args.reviewer!r}; have {sorted(revs)}", file=sys.stderr)
        return 2
    rev = revs[args.reviewer]
    report: dict = {"reviewer": rev.name, "model": rev.model, "family": rev.family, "surfaces": {}}
    for surface in args.surfaces.split(","):
        tools = tools_for(surface)
        rows, misses, tokens = [], [], []
        for sc in SCENARIOS:
            for _ in range(args.repeat):
                try:
                    name, raw, used = ask(rev, tools, sc.prompt, args.timeout)
                except Exception as exc:  # an unreachable or erroring model is a result
                    report["surfaces"][surface] = {"error": f"{type(exc).__name__}: {exc}"}
                    break
                tokens.append(used)
                g = grade(sc, surface, name, raw)
                if args.retry and name and not g.valid:
                    g, name, raw, extra = retry(rev, tools, sc, surface, name, raw, args.timeout, g)
                    tokens.append(extra)
                rows.append(g)
                if not (g.tool_ok and g.args_ok and g.valid):
                    misses.append({"prompt": sc.prompt, "want": sc.tool, "got": name, "args": raw})
            else:
                continue
            break
        else:
            report["surfaces"][surface] = {
                "bytes": size(tools),
                "tools": len(tools),
                "prompt_tokens_mean": round(sum(tokens) / max(len(tokens), 1)),
                **tally(rows),
                "misses": misses,
            }
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
