"""Delivery of a report bundle (D-upstream-reporting (4), (5)): file, prefilled URL, gh.

No network, never the real `gh`: every runner is a fake. Private fixture text is built from
string pieces; the real-text corpus is generated from this project's event log at test time.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from ddflow.core.events import Event
from ddflow.infra import upstream_gh as G
from ddflow.services import bugreport as B
from ddflow.services import install_info as I
from ddflow.services import upstream_delivery as D

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / ".ddflow" / "events"
HOME = "/" + "home" + "/" + "someone"
PROJECT = "secretproj"
ADDR = ".".join(["10", "1", "2", "3"])
REPO = "owner/repo"
INSTALL = I.InstallInfo(version="9.9.9", kind="vcs", commit="ab" * 20, is_own_dev_tree=False)
ENV_1KB = {
    "python": "3.12.1",
    "os": "Linux 6.8",
    "machine": "x86_64",
    "note": ("Linux 6.8 x86_64 glibc " * 50)[:1000],
}


def _bundle(title="claim crashes", expected="claim returns a lease", **over) -> B.Bundle:
    kw = {
        "title": title,
        "expected": expected,
        "install": INSTALL,
        "provenance": B.Provenance("agent-1", "someone", "s", "agent-judgement"),
        "events": [Event(kind="bug.found", subject="x", agent="agent-1", lamport=1)],
        "env": ENV_1KB,
        "scrub": B.Scrub(hostname="buildbox7", names=[PROJECT], home=HOME, repo_root="/r"),
    }
    kw.update(over)
    return B.build_bundle(**kw)


class Runner:
    """A fake gh. Records every call; never starts a process."""

    def __init__(self, auth=(0, "", ""), create=(0, f"https://github.com/{REPO}/issues/42\n", "")):
        self.calls: list[list[str]] = []
        self.auth, self.create = auth, create

    def __call__(self, repo, argv, **kw):
        self.calls.append(list(argv))
        code, out, err = self.auth if argv[1] == "auth" else self.create
        return subprocess.CompletedProcess(argv, code, out, err)


def _consent(b, **over):
    kw = {"digest": b.digest, "expires_at": 1000.0}
    kw.update(over)
    return D.Consent(**kw)


NOW = lambda: 500.0  # noqa: E731


# ---------------------------------------------------------------- file


def test_report_files_named_by_digest_owner_only_and_ignored(tmp_path):
    b = _bundle()
    md, js = D.write_report(tmp_path, b)
    assert md.name == f"{b.digest}.md" and js.name == f"{b.digest}.json"
    assert md.read_text() == b.rendered and js.read_text() == b.json
    d = D.reports_dir(tmp_path)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert stat.S_IMODE(md.stat().st_mode) == 0o600
    assert (tmp_path / ".ddflow" / "local" / ".gitignore").read_text().strip() == "*"


def test_redaction_precedes_the_disk(tmp_path):
    b = _bundle(
        title=f"crash in {PROJECT}",
        expected=f"{HOME}/src/{PROJECT}/x calling {ADDR}",
    )
    D.write_report(tmp_path, b)
    for f in D.reports_dir(tmp_path).iterdir():
        text = f.read_text()
        assert HOME not in text and PROJECT not in text and ADDR not in text


def test_a_bundle_that_does_not_match_its_digest_is_never_written(tmp_path):
    b = _bundle()
    bad = B.Bundle(b.data, b.body + "tampered", b.json, b.digest, b.redactions)
    with pytest.raises(ValueError):
        D.write_report(tmp_path, bad)
    assert not D.reports_dir(tmp_path).exists()


# ---------------------------------------------------------------- URL


def test_url_carries_exactly_the_previewed_text():
    b = _bundle(title="a & b = c")
    url, why = D.issue_url(b, REPO)
    assert why == "" and url.startswith(f"https://github.com/{REPO}/issues/new?title=")
    q = parse_qs(urlsplit(url).query, keep_blank_values=True)
    assert q["title"] == ["a & b = c"] and q["body"] == [b.rendered]
    assert "\n" not in url and " " not in url


def test_url_rejects_a_malformed_repo():
    for bad in ("owner/repo?x=1", "-owner/repo", "owner", "a/b/c", ""):
        with pytest.raises(ValueError):
            D.issue_url(_bundle(), bad)
    b = _bundle()
    r = Runner()
    out = D.send_gh(Path("."), b, "-owner/repo", _consent(b), runner=r, now=NOW)
    assert out.status == "refused" and r.calls == []


def _real_bug_texts() -> list[str]:
    out = []
    for f in sorted(EVENTS.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") == "bug.found":
                out.append(str((e.get("data") or {}).get("summary", "")))
    return out


def test_url_under_8kb_for_the_whole_real_text_corpus():
    texts = _real_bug_texts()
    assert len(texts) > 50
    biggest = 0
    for t in texts:
        b = _bundle(title=t[:100], expected=t, failure=B.Failure(argv=["ddflow", "x"], stderr=t))
        url, why = D.issue_url(b, REPO)
        assert url is not None, why
        biggest = max(biggest, len(url))
    assert biggest < 8192 and D.URL_MAX <= 8192


def test_oversize_url_falls_back_to_the_file_and_says_so(tmp_path):
    b = _bundle(expected="é" * 900, failure=B.Failure(argv=["ddflow"], stderr="é" * 3000))
    url, why = D.issue_url(b, REPO)
    assert url is None and "limit" in why
    out = D.prepare(tmp_path, b, REPO)
    assert out.status == "fallback" and out.exit_code == 0 and not out.url
    assert "attach the file" in out.reason and out.markdown_path.read_text() == b.rendered
    # nothing is silently cut: the file holds the whole report
    assert b.rendered.count("é") >= 3000


def test_prepare_writes_file_and_url_and_sends_nothing(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("preparation must not start a process")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    out = D.prepare(tmp_path, _bundle(), REPO)
    assert out.status == "prepared" and out.url and out.markdown_path.exists()


# ---------------------------------------------------------------- gh


def test_send_with_a_valid_consent_sends_the_previewed_bytes(tmp_path):
    b = _bundle()
    r = Runner()
    c = _consent(b)
    out = D.send_gh(tmp_path, b, REPO, c, runner=r, now=NOW)
    assert out.status == "sent" and out.exit_code == 0
    assert out.url == f"https://github.com/{REPO}/issues/42" and out.issue_number == 42
    assert [c_[:3] for c_ in r.calls] == [["gh", "auth", "status"], ["gh", "issue", "create"]]
    create = r.calls[1]
    assert create[create.index("-R") + 1] == REPO
    assert create[create.index("--title") + 1] == b.data["title"]
    sent = Path(create[create.index("--body-file") + 1])
    assert sent.read_bytes() == b.rendered.encode("utf-8")
    assert c.used


def test_send_is_single_use(tmp_path):
    b = _bundle()
    c = _consent(b)
    assert D.send_gh(tmp_path, b, REPO, c, runner=Runner(), now=NOW).status == "sent"
    r = Runner()
    again = D.send_gh(tmp_path, b, REPO, c, runner=r, now=NOW)
    assert again.status == "refused" and again.exit_code == 3 and r.calls == []


@pytest.mark.parametrize(
    "make",
    [
        lambda b: None,
        lambda b: _consent(b, digest="0" * 64),
        lambda b: _consent(b, expires_at=100.0),
        lambda b: _consent(b, used=True),
        lambda b: True,  # an agent-supplied flag is not a consent
    ],
    ids=["none", "other-digest", "expired", "used", "flag"],
)
def test_send_refused_without_a_valid_consent_calls_no_runner(tmp_path, make):
    b = _bundle()
    r = Runner()
    out = D.send_gh(tmp_path, b, REPO, make(b), runner=r, now=NOW)
    assert out.status == "refused" and out.exit_code == 3 and out.reason
    assert r.calls == []
    assert not D.reports_dir(tmp_path).exists()


def test_consent_for_the_old_text_does_not_cover_an_edit(tmp_path):
    old, new = _bundle(), _bundle(expected="something else")
    r = Runner()
    out = D.send_gh(tmp_path, new, REPO, _consent(old), runner=r, now=NOW)
    assert out.status == "refused" and r.calls == []


def test_a_tampered_bundle_is_refused_even_with_its_own_digest(tmp_path):
    b = _bundle()
    bad = B.Bundle(b.data, b.body + "x", b.json, b.digest, b.redactions)
    r = Runner()
    out = D.send_gh(tmp_path, bad, REPO, _consent(b), runner=r, now=NOW)
    assert out.status == "refused" and r.calls == []


def test_gh_missing_is_unavailable_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr("ddflow.infra.forge.shutil.which", lambda _n: None)
    b = _bundle()
    c = _consent(b)
    out = D.send_gh(tmp_path, b, REPO, c, now=NOW)  # the default runner: gh looked up, absent
    assert out.status == "unavailable" and out.exit_code == 2
    assert "gh" in out.reason and "not installed" in out.reason
    assert not c.used  # nothing was attempted, so the consent is kept


def test_gh_not_logged_in_is_unavailable(tmp_path):
    b = _bundle()
    r = Runner(auth=(1, "", "You are not logged into any GitHub hosts. Run gh auth login"))
    c = _consent(b)
    out = D.send_gh(tmp_path, b, REPO, c, runner=r, now=NOW)
    assert out.status == "unavailable" and "gh auth login" in out.reason
    assert len(r.calls) == 1 and not c.used


def test_a_gh_error_is_an_error_with_a_redacted_message(tmp_path):
    b = _bundle()
    r = Runner(create=(1, "", f"HTTP 422 for {ADDR} in {HOME}/x: validation failed"))
    out = D.send_gh(tmp_path, b, REPO, _consent(b), runner=r, now=NOW)
    assert out.status == "failed" and out.exit_code == 1
    assert "validation failed" in out.reason
    assert ADDR not in out.reason and HOME not in out.reason


def test_create_issue_parses_the_number(tmp_path):
    url, n = G.create_issue(tmp_path, REPO, "t", tmp_path / "f", runner=Runner())
    assert (url, n) == (f"https://github.com/{REPO}/issues/42", 42)


# ---------------------------------------------------------------- hygiene


def test_credentials_are_never_read_or_logged():
    for rel in ("ddflow/services/upstream_delivery.py", "ddflow/infra/upstream_gh.py"):
        src = (ROOT / rel).read_text()
        code = re.sub(r'""".*?"""', "", src, flags=re.S)
        for needle in ("token", "TOKEN", "os.environ", "getenv", "show-token", "keyring", "netrc"):
            assert needle not in code, f"{rel} mentions {needle}"
        for opener in ("webbrowser", "xdg-open", "urlopen", "requests", "http.client", "socket"):
            assert opener not in code, f"{rel} reaches for {opener}"
        assert "print(" not in code and "logging" not in code


def test_gh_not_logged_in_on_create_is_also_unavailable(tmp_path):
    b = _bundle()
    r = Runner(create=(1, "", "To get started with GitHub CLI, please run:  gh auth login"))
    out = D.send_gh(tmp_path, b, REPO, _consent(b), runner=r, now=NOW)
    assert out.status == "unavailable" and out.exit_code == 2 and "gh auth login" in out.reason


def test_prepare_failure_messages_are_redacted(tmp_path, monkeypatch):
    def broken(root, bundle):
        raise OSError(f"cannot write {HOME}/x for {ADDR}")

    monkeypatch.setattr(D, "write_report", broken)
    out = D.prepare(tmp_path, _bundle(), REPO)
    assert out.status == "failed" and out.exit_code == 1 and out.reason
    assert HOME not in out.reason and ADDR not in out.reason


def test_consume_is_serialised_by_the_consents_lock():
    """Within one process: holding the lock blocks a consumer (it fails without the lock),
    and the many-threads race yields exactly one winner."""
    import threading

    c = D.Consent("d" * 64, 1e18)
    done = threading.Event()
    with c._lock:
        t = threading.Thread(target=lambda: (c.consume(), done.set()))
        t.start()
        assert not done.wait(0.2) and not c.used
    t.join(5)
    assert c.used

    c = D.Consent("d" * 64, 1e18)
    wins: list[bool] = []
    barrier = threading.Barrier(16)

    def go():
        barrier.wait()
        wins.append(c.consume())

    ts = [threading.Thread(target=go) for _ in range(16)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert wins.count(True) == 1


def _project_with_redaction(tmp_path, body: str):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text(body)
    return tmp_path


def test_prepare_failure_messages_use_the_projects_redaction_patterns(tmp_path, monkeypatch):
    """Bug B9fad3e83d9: `_safe` built the redactor with no config, so a secret shape the
    project configured ([session].redact_extra) passed through a failure message."""
    root = _project_with_redaction(tmp_path, '[session]\nredact_extra = ["ZZSECRET-[0-9]+"]\n')

    def broken(root, bundle):
        raise OSError(f"cannot write ZZSECRET-12345 in {HOME}/x for {ADDR}")

    monkeypatch.setattr(D, "write_report", broken)
    out = D.prepare(root, _bundle(), REPO)
    assert out.status == "failed" and out.reason
    assert "ZZSECRET-12345" not in out.reason
    assert HOME not in out.reason and ADDR not in out.reason  # the profile's masks still apply


def test_failure_messages_fall_back_to_the_defaults_on_a_broken_config(tmp_path, monkeypatch):
    root = _project_with_redaction(tmp_path, "this is = not [valid toml")

    def broken(root, bundle):
        raise OSError(f"cannot write {HOME}/x for {ADDR}")

    monkeypatch.setattr(D, "write_report", broken)
    out = D.prepare(root, _bundle(), REPO)
    assert out.status == "failed" and HOME not in out.reason and ADDR not in out.reason
