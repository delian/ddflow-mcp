"""The `http-home` contract's blind spot: import-linter sees import ROOTS, this test sees names.

No module outside ddflow/adapters/http.py may import a way to open a connection.
"""

from __future__ import annotations

# -- the contract's blind spot: import-linter sees ROOTS (`urllib`, `http`) -------------

#: What a module outside the home may still import from the network roots, as (file, module,
#: name) -- name None for a plain `import`: string helpers, never a way to open a
#: connection. `urllib.parse` (either form) is allowed anywhere. `http-home`'s baseline lists
#: these modules only because the contract cannot tell `urllib.parse` from `urllib.request`;
#: this test can, so a baseline entry cannot hide a `urllib.request` or `http.client` import,
#: and the two odd ones are named per file.
_ALLOWED_OUTSIDE_HOME = {
    ("ddflow/services/install_info.py", "urllib.request", "url2pathname"),  # file: URL -> path
    ("ddflow/infra/hostinfo.py", "socket", None),  # gethostname: not HTTP; follow-up task
}


def _network_imports(source: str) -> list[tuple[str, str | None]]:
    import ast

    roots = ("urllib", "http", "socket")
    out: list[tuple[str, str | None]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out += [(a.name, None) for a in node.names if a.name.split(".")[0] in roots]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            if (node.module or "").split(".")[0] in roots:
                out += [(node.module or "", a.name) for a in node.names]
    return out


def _pure_helper(file: str, mod: str, name: str | None) -> bool:
    """`urllib.parse` in either form, or one of the named per-file exceptions."""
    return (
        mod.startswith("urllib.parse")
        or (mod, name) == ("urllib", "parse")
        or (file, mod, name) in _ALLOWED_OUTSIDE_HOME
    )


def test_no_module_outside_the_home_can_open_a_connection():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "ddflow"
    offenders = [
        f"{path.relative_to(root.parent)}: {mod}{'.' + name if name else ''}"
        for path in sorted(root.rglob("*.py"))
        if path != root / "adapters" / "http.py"
        for mod, name in _network_imports(path.read_text("utf-8"))
        if not _pure_helper(path.relative_to(root.parent).as_posix(), mod, name)
    ]
    assert not offenders, (
        "only ddflow/adapters/http.py may open connections (urllib.request, urllib.error, "
        f"http.client, socket): {offenders}. Use the HttpClient port."
    )


def test_the_blind_spot_check_sees_a_network_import_hiding_behind_an_allowed_root():
    assert sorted(
        _network_imports("import urllib.request\nfrom http.client import HTTPConnection\n"),
        key=str,
    ) == [("http.client", "HTTPConnection"), ("urllib.request", None)]
    assert _network_imports("from . import http\nimport json\n") == []
    assert _pure_helper("ddflow/a.py", "urllib", "parse")
    assert not _pure_helper("ddflow/a.py", "socket", None)  # only hostinfo may import socket
    assert not _pure_helper("ddflow/a.py", "urllib", "request")
    assert _pure_helper("ddflow/infra/hostinfo.py", "socket", None)
