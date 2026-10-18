"""Load the sensitivity vocabulary: the packaged levels, then the project's own over them.

``ddflow/templates/sensitivity.toml`` is the shipped default; ``<repo>/.ddflow/sensitivity.toml``
adds levels or replaces one of the same name (a project may make ``internal`` fit for a model,
or add ``secret-lab`` above ``confidential``). Parsing and the rules are `core.sensitivity`.
"""

from __future__ import annotations

from pathlib import Path

from ..core import sensitivity as S

PACKAGED = Path(__file__).resolve().parent.parent / "templates" / "sensitivity.toml"
#: Where a project keeps levels of its own, beside its config.
PROJECT_FILE = ".ddflow/sensitivity.toml"


def load(repo: Path | str | None = None) -> S.Vocabulary:
    """The vocabulary for ``repo`` (the packaged one alone when ``repo`` is None or has no file)."""
    texts = [PACKAGED.read_text(encoding="utf-8")]
    if repo is not None:
        own = Path(repo) / PROJECT_FILE
        if own.is_file():
            texts.append(own.read_text(encoding="utf-8"))
    return S.parse(*texts)
