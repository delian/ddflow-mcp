"""The schedule and trigger directory loaders share one loader (B-uni-overlay.5-tomldir).

The goldens below were captured from the two hand-rolled loaders BEFORE they were merged;
they must not change."""

from __future__ import annotations

from pathlib import Path

from ddflow.services import schedule as SV
from ddflow.services import triggers as TR
from ddflow.services.tomldir import load_toml_dir

SCHEDULE_FILES = {
    "good.toml": 'title = "Good"\ncadence = { every_days = 2 }\n',
    "second.toml": 'cadence = { every_tasks = 3 }\nneeds = ["good"]\n',
    "broken.toml": "title = = 1\n",
    "wrongid.toml": 'id = "other"\ncadence = { every_days = 1 }\n',
    "badfield.toml": "cadence = { every_days = 1 }\nbogus = 1\n",
    "nocadence.toml": 'title = "x"\n',
    "Bad.Id.toml": "cadence = { every_days = 1 }\n",
    "notes.txt": "ignored",
}
TRIGGER_FILES = {
    "ok.toml": 'event = "gate.*"\naction = { job = "good" }\n',
    "nojob.toml": 'event = "gate.*"\naction = { job = "ghost" }\n',
    "broken.toml": "event = \n",
    "wrongid.toml": 'id = "other"\nevent = "x"\naction = { job = "good" }\n',
    "noevent.toml": 'action = { job = "good" }\n',
    "secs.toml": 'event = "x"\naction = { job = "good" }\ncooldown_s = "30m"\n',
    "both.toml": 'event = "x"\naction = { job = "good" }\ncooldown = 1\ncooldown_s = 60\n',
}

GOLDEN_SCHEDULE_ERRORS = [
    ".ddflow/schedules/Bad.Id.toml: job id 'Bad.Id' must be letters, digits, '_' and '-' only (a plain TOML key, D-plain-keys)",
    ".ddflow/schedules/badfield.toml: unknown field(s) bogus: a job has title, cadence, needs, scope_globs, concurrency_group, prompt, mode, budget, escalate, missed, jitter, enabled, tags",
    ".ddflow/schedules/broken.toml: cannot read it: Invalid value (at line 1, column 9)",
    ".ddflow/schedules/nocadence.toml: cadence is required: one of every_days, every_tasks, every_phases",
    ".ddflow/schedules/wrongid.toml: id 'other' differs from the file name; a job file is <id>.toml",
]
GOLDEN_TRIGGERS = {
    "ok": ("gate.*", {"job": "good"}, 60),
    "secs": ("x", {"job": "good"}, 30),
}
GOLDEN_TRIGGER_ERRORS = [
    ".ddflow/triggers/both.toml: give cooldown (minutes) or cooldown_s (seconds), not both",
    ".ddflow/triggers/broken.toml: cannot read it: Invalid value (at line 1, column 9)",
    ".ddflow/triggers/noevent.toml: event is required",
    ".ddflow/triggers/nojob.toml: action.job 'ghost' is not a scheduled job",
    ".ddflow/triggers/wrongid.toml: its id differs from the file name; a trigger file is <id>.toml",
]


def _write(repo: Path, rel: Path, files: dict[str, str]) -> None:
    d = repo / rel
    d.mkdir(parents=True)
    for name, text in files.items():
        (d / name).write_text(text, "utf-8")


def test_schedule_files_golden(tmp_path):
    _write(tmp_path, SV.SCHEDULES_DIR, SCHEDULE_FILES)
    jobs, errors = SV.load_files(tmp_path)
    assert [(j.id, j.title, j.cadence, j.needs, src) for j, src in jobs] == [
        ("good", "Good", {"every_days": 2}, [], "file:.ddflow/schedules/good.toml"),
        ("second", "", {"every_tasks": 3}, ["good"], "file:.ddflow/schedules/second.toml"),
    ]
    assert errors == GOLDEN_SCHEDULE_ERRORS


def test_trigger_files_golden(tmp_path):
    _write(tmp_path, TR.TRIGGERS_DIR, TRIGGER_FILES)
    trigs, errors = TR.load(tmp_path, {"good": object()})
    assert {k: (t.event, t.action, t.cooldown) for k, t in trigs.items()} == GOLDEN_TRIGGERS
    assert errors == GOLDEN_TRIGGER_ERRORS


def test_missing_directory_is_empty(tmp_path):
    assert SV.load_files(tmp_path) == ([], [])
    assert TR.load(tmp_path, {}) == ({}, [])


def test_load_toml_dir_contract(tmp_path):
    _write(tmp_path, Path("d"), {"a.toml": "x = 1\n", "b.toml": "x = 2\n", "c.toml": "x = =\n"})
    seen = []

    def build(stem, spec):
        seen.append((stem, spec))
        return (None, ["no"]) if stem == "b" else (spec["x"], [])

    out, errors = load_toml_dir(tmp_path, Path("d"), build, lambda i: f"{i} mismatch")
    assert out == [("a", 1, "d/a.toml")]
    assert [s for s, _ in seen] == ["a", "b"]
    assert errors[0] == "d/b.toml: no"
    assert errors[1].startswith("d/c.toml: cannot read it: ")
