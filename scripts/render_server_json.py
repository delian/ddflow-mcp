#!/usr/bin/env python3
"""Render server.json from server.template.json and the version in ddflow/__init__.py.

    scripts/render_server_json.py            # write server.json
    scripts/render_server_json.py --check    # exit 1 if server.json is not the render

`ddflow/__init__.py` holds the ONE hand-edited version literal. server.json is a generated,
committed artifact: the template carries `@VERSION@` wherever the version appears (the
manifest's `version`, the `pypi` package's `version`, the tag of every `oci` identifier --
an `oci` package has NO `version` field, the registry rejects one). Edit the template, never
server.json. Stdlib only, so CI can run it before anything is installed.

The version is read from the file's text, not imported: an import trusts __pycache__ and
whatever copy of ddflow happens to be first on sys.path.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PLACEHOLDER = "@VERSION@"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.]+)?$")


def declared_version(root: Path) -> str:
    text = (root / "ddflow" / "__init__.py").read_text("utf-8")
    found = re.findall(r'^__version__ = "([^"]+)"$', text, re.M)
    if len(found) != 1:
        sys.exit(
            f'ddflow/__init__.py: expected exactly one `__version__ = "..."`, found {len(found)}'
        )
    if not VERSION_RE.match(found[0]):
        sys.exit(f"ddflow/__init__.py: {found[0]!r} is not a version")
    return found[0]


def render(root: Path) -> str:
    template = (root / "server.template.json").read_text("utf-8")
    if PLACEHOLDER not in template:
        sys.exit(f"server.template.json has no {PLACEHOLDER}")
    out = template.replace(PLACEHOLDER, declared_version(root))
    json.loads(out)  # a template that renders to invalid JSON must not be written
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if server.json is not the render")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    a = ap.parse_args(argv)
    target = a.root / "server.json"
    want = render(a.root)
    if a.check:
        have = target.read_text("utf-8") if target.exists() else None
        if have != want:
            print(
                "server.json is not the render of server.template.json at "
                f"{declared_version(a.root)}; run scripts/render_server_json.py",
                file=sys.stderr,
            )
            return 1
        return 0
    target.write_text(want, "utf-8")
    print(f"server.json rendered at {declared_version(a.root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
