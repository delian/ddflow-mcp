"""B-uni-fsio-digest: every content digest goes through `core.digest`, byte for byte as before.

Each digest below is STORED somewhere -- an event id, a ledger, a generated file's
header, a reviewer's identity, a queue file name -- so moving it onto `core.digest` must
not change one character: an old record would read as changed. Each case recomputes the
digest the way the site did before the move (`hashlib` directly, the algorithm, size,
length and encoding error handler it used) and compares, over inputs that exercise the
encoding (non-ASCII, a lone surrogate where the site's handler allows one).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ddflow.core import digest as D

TEXTS = ["", "plain", "naïve — ünïcode ✓", "line\nbreaks\tand\x00nul"]


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def blake(b: bytes, size: int) -> str:
    return hashlib.blake2b(b, digest_size=size).hexdigest()


# -- the primitive ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", TEXTS)
def test_content_digest_is_hashlib(text):
    b = text.encode("utf-8")
    assert D.content_digest(text) == sha256(b)
    assert D.content_digest(b) == sha256(b)
    assert D.content_digest(text, length=12) == sha256(b)[:12]
    assert D.content_digest(text, "sha1") == hashlib.sha1(b).hexdigest()
    for size in (4, 5, 8, 10, 12, 16):
        assert D.content_digest(text, "blake2b", size=size) == blake(b, size)


def test_the_error_handlers_reach_the_encoding():
    lone = "a\udc80b"
    assert D.content_digest(lone, errors="replace") == sha256(lone.encode("utf-8", "replace"))
    assert D.content_digest(lone, errors="surrogateescape") == sha256(
        lone.encode("utf-8", "surrogateescape")
    )
    with pytest.raises(UnicodeEncodeError):
        D.content_digest(lone)


def test_a_hasher_continues_like_hashlib():
    h = D.hasher(b"ab")
    h.update(b"cd")
    assert h.hexdigest() == sha256(b"abcd")


# -- every moved site, against its old expression ----------------------------------------------


@pytest.mark.parametrize("text", TEXTS)
def test_core_sites(text, monkeypatch):
    from ddflow.core import events, ids, textsim

    obj = {"t": text, "n": 1}
    assert events.canonical_digest(obj) == blake(events.canonical(obj).encode(), 12)
    ev = events.Event(kind="task.added", subject="T1", data=obj, lamport=3, ts="t")
    assert ev.compute_id() == "e" + blake(events.canonical(ev.body()).encode(), 12)

    monkeypatch.setattr(ids.time, "time_ns", lambda: 42)
    assert ids.auto_id("L", text, "x") == "L" + blake(f"{text}|x|42".encode(), 5)
    assert ids.refile("B1", text) == "B1-" + (
        text[:7].lower() if len(text) >= 7 and all(c in "0123456789abcdef" for c in text[:7].lower())
        else blake(text.encode(), 4)[:7]
    )  # fmt: skip
    norm = " ".join(f"{text}\nbody".casefold().split())
    assert textsim.digest(text, "body") == blake(norm.encode(), 10)


@pytest.mark.parametrize("text", TEXTS)
def test_service_sites(text, tmp_path):
    from ddflow.services import ci, ledger, quota, review, reviewer_trust, sessions
    from ddflow.services.bugreport import digest_of
    from ddflow.services.export.frame import DIGEST_LEN, body_digest
    from ddflow.services.export.registry import template_digest
    from ddflow.services.gates import evidence

    b = text.encode("utf-8")
    assert body_digest(text) == sha256(b)[:DIGEST_LEN]
    assert template_digest(text) == sha256(b)[:12]
    assert quota.account_tag(text) == sha256(b)[:12]
    assert evidence.digest(text) == blake(text.encode("utf-8", "replace"), 8)
    assert evidence._blob_id(b, "sha256") == sha256(b"blob %d\0" % len(b) + b)
    assert evidence._blob_id(b, "sha1") == hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()
    entries = {"a.py": ("100644", "abc"), text or "x": ("100755", "def")}
    body = "\n".join(f"{m} {o} {p}" for p, (m, o) in sorted(entries.items()))
    assert evidence.content_id(entries) == "st:" + blake(
        body.encode("utf-8", "surrogateescape"), 16
    )
    joined = "\x00".join([text, "body", "a", "b"])
    assert (
        ledger.requirement_digest(text, "body", ["b", "a"])
        == sha256(joined.encode("utf-8", "surrogatepass"))[:12]
    )
    fd = "\x1f".join(("HIGH", "loc", text, "detail"))
    assert (
        review.finding_digest("high", " loc ", text, "detail")
        == sha256(fd.encode("utf-8", "replace"))[:16]
    )
    assert review.diff_digest(text) == sha256(
        review.strip_hunk_context(text).encode("utf-8", "replace")
    )
    assert digest_of(text, "{}") == sha256(b + b"\n--json--\n" + b"{}")
    slug_digest = hashlib.sha1(text.encode()).hexdigest()[:10]
    assert slug_digest in ci.bug_id(text)
    raw = text + "/needs-sanitising"
    got = sessions.harness_session_id(raw)
    assert not got or got.endswith("-" + sha256(raw.encode("utf-8"))[:16])

    class Rev:
        name, kind, base_url, model, family, command = text, "openai", "u", "m", "f", ""

    ident = {k: str(getattr(Rev, k, "") or "") for k in reviewer_trust.IDENTITY}
    from ddflow.core.events import canonical

    assert reviewer_trust.digest(Rev) == sha256(canonical(ident).encode("utf-8"))[:16]

    f = tmp_path / "f.bin"
    f.write_bytes(b * 1000)
    from ddflow.services.legacy import sha256_file

    assert sha256_file(f) == sha256(b * 1000)


def test_queue_file_names_keep_their_digest(tmp_path):
    from ddflow.services import waits

    agent, item = "a/b", "T\udc801"
    want = hashlib.sha1(
        f"{agent}\0{item}".encode("utf-8", "surrogateescape"), usedforsecurity=False
    ).hexdigest()[:10]
    assert waits._queue_path(tmp_path, agent, item).name.endswith(f"-{want}.json")


def test_a_trigger_fingerprint_keeps_its_legacy_spelling():
    from dataclasses import asdict

    from ddflow.services import triggers

    t = triggers.Trigger(id="t1") if hasattr(triggers, "Trigger") else None
    if t is None:
        pytest.skip("no Trigger dataclass")
    d = asdict(t)
    d.pop("enabled", None)
    # a fire recorded before the canonical form carries the old spelling: still this definition
    assert t.legacy_digest() == sha256(json.dumps(d, sort_keys=True).encode())[:12]
    assert t.same_definition(t.legacy_digest()) and t.same_definition(t.digest())
    assert t.digest() == D.of_obj(d, size=6) and t.digest() != t.legacy_digest()


def test_a_docs_report_digest_is_unchanged(tmp_path):
    import subprocess

    from ddflow.services import docscheck

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("# x\n\nSee [missing](nope.md).\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    report = docscheck.check_docs(tmp_path)
    blob = json.dumps(
        {
            "findings": [f.as_dict() for f in report.findings],
            "notes": [n.as_dict() for n in report.notes],
            "checked": report.checked,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert report.digest == sha256(blob.encode())


def test_hashlib_is_imported_only_by_core_digest():
    root = Path(__file__).resolve().parents[1] / "ddflow"
    users = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "hashlib" in p.read_text("utf-8") and p.name != "digest.py"
        and any(ln.strip().startswith(("import hashlib", "from hashlib")) for ln in p.read_text("utf-8").splitlines())
    )  # fmt: skip
    assert users == [], users
