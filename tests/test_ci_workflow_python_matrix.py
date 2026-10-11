"""The `tests` job's python matrix must actually select the interpreter (B-ci-matrix-unused).

The matrix declared 3.11 and 3.13 but no step read `${{ matrix.python }}`, so `uv run`
used whatever it found: the floor and the release were never exercised and 3.12 (what
a contributor's CI hit) was absent. YAML is not a dependency, so the job is read as text.
"""

import re
from pathlib import Path

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _tests_job() -> str:
    text = CI.read_text()
    m = re.search(r"^  tests:\n(.*?)(?=^  \w[\w-]*:\n)", text, re.S | re.M)
    assert m, "no `tests:` job in ci.yml"
    return m.group(1)


def _matrix_versions(job: str) -> list[str]:
    m = re.search(r"^\s+python:\s*\[(.*?)\]", job, re.M)
    assert m, "tests job has no matrix.python"
    return re.findall(r"[\"']?(\d+\.\d+)[\"']?", m.group(1))


def test_matrix_covers_floor_middle_and_release():
    versions = _matrix_versions(_tests_job())
    assert "3.11" in versions and "3.12" in versions and "3.13" in versions


def test_every_uv_run_in_tests_job_selects_the_matrix_interpreter():
    job = _tests_job()
    runs = [ln for ln in job.splitlines() if re.search(r"\buv run\b", ln)]
    assert runs, "tests job has no `uv run` step"
    for ln in runs:
        assert "--python ${{ matrix.python }}" in ln, f"uses the default interpreter: {ln.strip()}"
