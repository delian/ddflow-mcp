"""Reviewer HTTP calls reach http(s) endpoints only.

`urllib.request.urlopen` follows any scheme it knows, including `file:`. Reviewer URLs
come from configuration, so a `base_url = "file:///etc"` -- a typo, or a config written
by someone else -- would have read local files into a request and a "review". Flagged by
bandit (B310) in CI's static scan, which no run had reached: the build step before it
failed on every run.
"""

from __future__ import annotations

import urllib.error

import pytest

from ddflow.services import review as R


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "data:,hi"])
def test_a_non_http_scheme_is_refused_before_anything_is_opened(url):
    with pytest.raises(urllib.error.URLError, match="only http"):
        R._open(url, timeout=1)


def test_the_models_probe_treats_a_file_url_as_unreachable(tmp_path):
    secret = tmp_path / "models"
    secret.write_text('{"data": [{"id": "leaked-from-disk"}]}')
    assert R.probe_endpoint(f"file://{tmp_path}") == []
