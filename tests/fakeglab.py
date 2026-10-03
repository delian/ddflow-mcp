"""A stand-in for the `glab` CLI's `api` verb, over one JSON state file.

Only the discussion endpoints (review threads) are faked: that is the adapter surface the
tests need it for. The state is `{"discussions": [...], "calls": [...]}` in GitLab's own
REST shape, so the adapter is exercised against the documented response.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

STATE_ENV = "FAKEGLAB_STATE"


def main(argv: list[str]) -> int:
    path = Path(os.environ[STATE_ENV])
    st = json.loads(path.read_text())
    st.setdefault("calls", []).append(argv)

    def done(out: object = None, code: int = 0, err: str = "") -> int:
        path.write_text(json.dumps(st, indent=2))
        if out is not None:
            print(json.dumps(out))
        if err:
            print(err, file=sys.stderr)
        return code

    if argv[0] != "api":
        return done(code=2, err=f"fake glab: unsupported {argv}")
    method = argv[argv.index("-X") + 1] if "-X" in argv else "GET"
    endpoint = next(a for a in argv[1:] if a.startswith("projects/"))
    fields = dict(a.split("=", 1) for a in argv if "=" in a and not a.startswith("projects"))
    parts = endpoint.split("/")  # projects/:id/merge_requests/<n>/discussions[/<id>[/notes]]
    if "discussions" not in parts:
        return done(code=2, err=f"fake glab: unsupported {endpoint}")
    rest = parts[parts.index("discussions") + 1 :]
    if method == "GET" and not rest:
        return done(st["discussions"])
    disc = next((d for d in st["discussions"] if d["id"] == rest[0]), None)
    if disc is None:
        return done(code=1, err="404 Not Found")
    if method == "POST" and rest[1:] == ["notes"]:
        disc["notes"].append(
            {"body": fields["body"], "author": {"username": "agent"}, "system": False}
        )
        return done({"id": 99})
    if method == "PUT" and len(rest) == 1:
        for note in disc["notes"]:
            if note.get("resolvable"):
                note["resolved"] = fields.get("resolved") == "true"
        return done(disc)
    return done(code=2, err=f"fake glab: unsupported {method} {endpoint}")


def install(tmp: Path, discussions: list[dict]) -> tuple[Path, Path]:
    """Write the fake `glab` and its state file. Returns (bin_dir, state_file)."""
    bindir = tmp / "fakeglab-bin"
    bindir.mkdir(exist_ok=True)
    state = tmp / "glab.json"
    state.write_text(json.dumps({"discussions": discussions, "calls": []}))
    exe = bindir / "glab"
    exe.write_text(
        f"#!{sys.executable}\n"
        f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "import fakeglab; sys.exit(fakeglab.main(sys.argv[1:]))\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return bindir, state
