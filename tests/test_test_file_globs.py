"""`lessons.test_file_globs` recognises the test layouts of other languages (bug Bbdd33740d6)."""

from __future__ import annotations

import pytest

from ddflow.config import Config
from ddflow.services.gates.regression_testfix import _is_test_file

TEST_FILES = [
    "tests/test_x.py",
    "pkg/test_x.py",
    "pkg/conftest.py",
    "web/src/app.test.ts",
    "app.test.js",
    "web/src/app.spec.tsx",
    "pkg/store_test.go",
    "store_test.go",
    "crates/core/tests/api.rs",
    "tests/api.rs",
    "spec/models/user_spec.rb",
    "lib/user_spec.rb",
    "web/src/__tests__/app.js",
    "__tests__/app.js",
    "spec/helper.rb",
]
SOURCE_FILES = [
    "src/main.rs",
    "web/src/app.ts",
    "pkg/store.go",
    "ddflow/services/gates/runner.py",
    "docs/inspector.md",
    "lib/contest.py",
]


@pytest.mark.parametrize("path", TEST_FILES)
def test_a_test_file_of_any_common_layout_is_recognised(path):
    assert _is_test_file(path, list(Config.load().lessons.test_file_globs)), path


@pytest.mark.parametrize("path", SOURCE_FILES)
def test_a_source_file_is_not_taken_for_a_test_file(path):
    assert not _is_test_file(path, list(Config.load().lessons.test_file_globs)), path
