"""The README and the guides it points at, read as one body of documentation.

The README is the front door; the reference moved to docs/guide/*.md (D-readme-split). A test
that asks whether the docs *say* something reads this; a test of the generated regions or of
the front door itself reads README.md alone.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


#: The guides the README points at (D-readme-split). Other files in docs/guide/ are not part
#: of the split and are not read as README text.
GUIDES = (
    "workflow",
    "adoption",
    "contributing",
    "cli-mcp",
    "configuration",
    "customisation",
    "knowledge",
    "upgrading",
    "parallelism",
    "gitflow",
)


def guide_files() -> list[Path]:
    return [ROOT / "docs" / "guide" / f"{g}.md" for g in GUIDES]


def readme_corpus() -> str:
    return "\n".join([(ROOT / "README.md").read_text("utf-8"), *(p.read_text("utf-8") for p in guide_files())])
