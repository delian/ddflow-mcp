"""B-upstream-dedupe: the local + upstream duplicate check for a report.

No test touches the network: the fetch function is injected, and `NoNetwork` proves the
real one is never reached. Fixture texts are four real filings of one ddflow bug.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

import pytest

from ddflow.services import bugreport as B
from ddflow.services import similar
from ddflow.services import upstream_dedupe as U

REPO = "someowner/somerepo"

# The four real filings of "update --globs does not widen the held lease".
FILINGS = {
    "B5d98a4da0a": "ddflow update <id> --globs widens the ITEM's globs but not the held lease: after 'update B17 --globs ...,README.md' the commit hook still said 'You hold: <old globs>' and warned README.md is uncovered — while its own hint names 'ddflow update <ID> --globs' as the way to widen an existing claim.",
    "B78324ec086": "ddflow update --globs widens the ITEM's globs but not the held LEASE's (core/model.py _h_updated sets it.globs only), while the commit hook checks lease.globs — so the documented 'widen before writing' step never reaches the hook: on B190, README.md was added to the item via ddflow_update and the next commit still warned 'not covered by a lease you hold'. With commit_without_lease=block it refuses. Probe: claim X --globs a; update X --globs a,b; commit b.",
    "Bbbcb91599c": "'ddflow update <ID> --globs' does not widen a HELD lease, though the pre-commit hook's own remedy says it does ('ddflow update <ID> --globs ...  # widen an existing claim'). The item's globs change; lease.globs stays as claimed, so the hook keeps warning (would refuse under commit_without_lease=block) and anything that conflict-checks against lease globs does not see the new path. Seen 2026-09-28 on B-bugs-first: update added ddflow/templates/prompts/help/parallel.md; show --json -> item globs has it, lease globs does not; commit hook warned 'not covered by a lease you hold'. Probe: claim X --globs a; update X --globs a,b; stage b; git commit -> warning.",
    "B3eda99e0fe": "ddflow update --globs on a CLAIMED item widens item.globs but not lease.globs; the commit hook (and lease-based conflict checks) read lease.globs, so the documented remedy 'ddflow update <ID> --globs' for an uncovered commit does nothing. Probe: claim B22 (lease globs 2 paths), ddflow_update B22 globs=11 paths, show B22 -> item 11 / lease 2; git commit -> 'ddflow: 8 staged path(s) are not covered by a lease you hold' (2026-09-28, commit 6072b39).",
}
UNRELATED = [
    (
        "Reviewer launch table is ignored when the model name has a slash",
        "The [[reviewer]] table with a launch command drops the model argument whenever the model id contains a slash, so the reviewer starts with its default.",
    ),
    (
        "Gate record refuses an author-family model on a reviewer gate",
        "complete refuses the gate outcome recorded with the author's model even though the reviewer was a different family.",
    ),
    (
        "Dashboard board column wraps badly on narrow terminals",
        "ddflow board prints truncated phase names when the terminal is under eighty columns wide.",
    ),
    (
        "replay misses decisions recorded after a session end",
        "ddflow replay skips the decision events when the session ended event precedes them in the shard.",
    ),
]


def issue(n, title, body="", **extra):
    return {
        "number": n,
        "title": title,
        "body": body,
        "html_url": f"https://github.com/{REPO}/issues/{n}",
        **extra,
    }


def corpus():
    reworded = [
        issue(7, "update --globs does not widen the lease", FILINGS[k])
        for k in ("B78324ec086", "Bbbcb91599c", "B3eda99e0fe")
    ][:1]
    unrelated = [issue(10 + i, t, b) for i, (t, b) in enumerate(UNRELATED)]
    return reworded + unrelated


class Fake:
    """The injected fetcher: serves pages of issues, records every URL asked."""

    def __init__(self, pages=None, *, status=200, headers=None, raises=None, raw=None):
        self.pages = pages or []
        self.status, self.headers, self.raises, self.raw = status, headers or {}, raises, raw
        self.calls: list[str] = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if self.raises:
            raise self.raises
        page = int(url.rsplit("page=", 1)[1].split("&")[0])
        body = (
            self.raw
            if self.raw is not None
            else json.dumps(self.pages[page - 1] if page <= len(self.pages) else [])
        )
        return U.Response(self.status, self.headers, body)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a test reached the real network")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def test_reworded_duplicate_scores_over_ask_and_offers_comment():
    f = Fake([corpus()])
    res = U.check(FILINGS["B5d98a4da0a"], "", repo=REPO, fetch=f)
    assert res.upstream_state == "checked" and res.status == "ok"
    top = res.upstream[0]
    assert top.id == "#7" and top.score >= 0.55 and top.band == "ask"
    assert res.offer is top and top.url == f"https://github.com/{REPO}/issues/7"
    assert "add a comment to #7" in res.summary() and top.url in res.summary()
    assert all("api.github.com/repos/someowner/somerepo/issues" in u for u in f.calls)


@pytest.mark.parametrize("other", ["B78324ec086", "Bbbcb91599c"])
def test_each_other_filing_is_a_duplicate_of_the_report(other):
    issues = [issue(7, "x", FILINGS[other])] + [
        issue(10 + i, t, b) for i, (t, b) in enumerate(UNRELATED)
    ]
    res = U.check(FILINGS["B5d98a4da0a"], "", repo=REPO, fetch=Fake([issues]))
    assert res.upstream[0].id == "#7" and res.upstream[0].score >= 0.55


def test_unrelated_report_is_not_offered():
    res = U.check(
        "Installer fails on Windows paths",
        "setup crashes with a backslash",
        repo=REPO,
        fetch=Fake([corpus()]),
    )
    assert res.offer is None and res.upstream_state == "checked"
    assert all(h.band == "show" for h in res.upstream)


def test_between_floor_and_threshold_is_listed_not_offered():
    res = U.check(
        FILINGS["B5d98a4da0a"],
        "",
        repo=REPO,
        fetch=Fake([corpus()]),
        ask_threshold=0.99,
        show_floor=0.2,
    )
    assert res.offer is None and res.upstream and all(h.band == "show" for h in res.upstream)


def test_empty_upstream_is_a_fact_not_unavailable():
    res = U.check("anything", "at all", repo=REPO, fetch=Fake([[]]))
    assert res.upstream_state == "no_issues" and res.status == "ok"
    assert "no upstream issues" in res.summary()
    assert "unavailable" not in res.summary()


def test_pull_requests_are_not_issues():
    pr = issue(
        3,
        "update --globs does not widen the lease",
        FILINGS["B78324ec086"],
        pull_request={"url": "x"},
    )
    res = U.check(FILINGS["B5d98a4da0a"], "", repo=REPO, fetch=Fake([[pr]]))
    assert res.upstream_state == "no_issues"


@pytest.mark.parametrize(
    "fake, why",
    [
        (Fake(raises=OSError("Network is unreachable")), "unreachable"),
        (Fake(raises=TimeoutError("timed out")), "timed out"),
        (Fake(status=403, headers={"X-RateLimit-Remaining": "0"}), "rate limit"),
        (Fake(status=429, headers={"Retry-After": "60"}), "rate limit"),
        (Fake(status=403), "403"),
        (Fake(status=404), "404"),
        (Fake(status=503), "503"),
        (Fake(raw="<html>not json</html>"), "unparseable"),
        (Fake(raw='{"message": "x"}'), "unparseable"),
        (Fake(raw="[1, 2, 3]"), "unparseable"),
        (Fake(raw='[{"title": "no number"}]'), "unparseable"),
    ],
)
def test_failure_is_unavailable_never_no_duplicates(fake, why):
    res = U.check("a report", "body", repo=REPO, fetch=fake)
    assert res.status == "unavailable" and res.upstream_state == "unavailable"
    assert why in res.reason.lower() and res.offer is None
    text = res.summary().lower()
    assert (
        "unavailable" in text and "no duplicates" not in text and "no upstream issues" not in text
    )


def test_failure_on_a_later_page_discards_the_partial_answer():
    class Second(Fake):
        def __call__(self, url, timeout):
            if "page=2" in url:
                return U.Response(429, {}, "")
            return super().__call__(url, timeout)

    first = [issue(n, f"t{n}", "b") for n in range(1, 101)]
    res = U.check("t", "b", repo=REPO, fetch=Second([first]))
    assert res.status == "unavailable" and res.upstream == []


def test_pagination_is_capped_and_reported():
    full = [[issue(p * 100 + n, f"t{p}{n}", "b") for n in range(100)] for p in range(10)]
    f = Fake(full)
    res = U.check("t", "b", repo=REPO, fetch=f, max_pages=2)
    assert len(f.calls) == 2 and res.truncated and "first 200" in res.summary()


def test_stops_when_a_short_page_ends_the_listing():
    f = Fake([[issue(1, "a", "b")]])
    U.check("t", "b", repo=REPO, fetch=f)
    assert len(f.calls) == 1


def test_low_rate_limit_remaining_stops_paging_as_unavailable():
    full = [[issue(n, f"t{n}", "b") for n in range(1, 101)]]
    f = Fake(full, headers={"X-RateLimit-Remaining": "0"})
    res = U.check("t", "b", repo=REPO, fetch=f)
    assert res.status == "unavailable" and "rate limit" in res.reason.lower()


@pytest.mark.parametrize("repo", ["", "nope", "a/b/c", "a/b;rm", "https://x/y", "-a/b"])
def test_bad_repo_is_refused_before_any_io(repo):
    f = Fake([[]])
    with pytest.raises(ValueError):
        U.check("t", "b", repo=repo, fetch=f)
    assert f.calls == []


def test_default_repo_and_thresholds():
    assert U.DEFAULT_REPO == "delian/ddflow-mcp"
    assert (U.ASK_THRESHOLD, U.SHOW_FLOOR) == (0.55, 0.35)


def test_real_fetch_transport_failure_is_unavailable(monkeypatch):
    def refuse(req, timeout):
        assert req.full_url.startswith("https://api.github.com/") and not req.data  # a GET
        raise urllib.error.URLError("Network is unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    res = U.check("t", "b", repo=REPO)  # default fetch = the real one, on a stub transport
    assert res.status == "unavailable" and "unreachable" in res.reason


def test_real_fetch_http_error_is_classified(monkeypatch):
    def limited(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 403, "rate", {"X-RateLimit-Remaining": "0"}, None
        )

    monkeypatch.setattr(urllib.request, "urlopen", limited)
    res = U.check("t", "b", repo=REPO)
    assert res.status == "unavailable" and "rate limit" in res.reason


def test_local_hits_are_checked_without_the_network():
    local = similar.build(
        [
            {"id": k, "kind": "bug", "title": v, "body": ""}
            for k, v in FILINGS.items()
            if k != "B5d98a4da0a"
        ]
        + [
            {"id": f"U{i}", "kind": "bug", "title": t, "body": b}
            for i, (t, b) in enumerate(UNRELATED)
        ]
    )
    f = Fake([[]])
    res = U.check(FILINGS["B5d98a4da0a"], "", repo=REPO, fetch=f, local=local)
    assert res.local and res.local[0].source == "local" and res.local[0].score >= 0.55
    assert res.local[0].band == "ask" and res.offer is None
    assert res.local[0].kind == "bug" and "bug B78324ec086" in res.summary()
    assert res.upstream_state == "no_issues"

    # a local failure is its own state; it never reads as "nothing local"
    class Broken:
        def query(self, *a, **k):
            raise LookupError("index missing")

    res = U.check("t", "b", repo=REPO, fetch=Fake([[]]), local=Broken())
    assert res.local_state == "unavailable" and res.status == "unavailable"
    res = U.check("t", "b", repo=REPO, fetch=Fake([[]]))
    assert res.local_state == "not_checked"


def test_report_text_comes_from_the_bundle():
    bundle = B.build_bundle(
        title="update --globs does not widen the held lease",
        expected="the lease globs follow the item globs",
        install=B._install.InstallInfo(
            version="1.0", kind="source", commit="abc", is_own_dev_tree=False
        ),
        provenance=B.Provenance(reported_by_agent="x", detector="agent-judgement"),
    )
    title, body = U.report_text(bundle)
    assert title == bundle.data["title"] and body == bundle.data["expected"]
