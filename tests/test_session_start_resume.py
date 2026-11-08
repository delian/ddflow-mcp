"""DX-recover-brief-slice: the SessionStart output starts with the RESUME block
(folded B-res-brief-recovery-link): recoverable work comes before the digest, the
current item and the queue, so an agent that restarts after a crash acts on it first.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog

RESUME = "## ⚠ RESUME: recoverable work found"


def _seed(repo: Path, ids: list[str]) -> EventLog:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    for t in ids:
        log.append("task.added", t, {"parent": "P1", "title": t, "globs": [f"src/{t}.py"]})
    return log


def test_the_session_start_output_opens_with_the_resume_block(repo):
    log = _seed(repo, ["T1", "T9"])
    log.append("item.started", "T1", {})  # RUNNING with nobody on it: interrupted
    code, out, err = run_cli(repo, "hooks", "session-start")
    assert code == 0, err
    # The hook's DELIVERED text is its stdout: the first section it names is the RESUME
    # block (duck #4) -- a preamble flushed elsewhere does not count as delivery.
    text = out
    first = re.search(r"^## ", text, re.M)
    assert first is not None, text
    assert text[first.start() :].startswith(RESUME), text
    # The interrupted item is INSIDE the leading block: after the RESUME heading and
    # before the pack's own H1 and anything new (duck #3/#7).
    assert text.index(RESUME) < text.index("**T1** (stale_running"), text
    assert text.index("**T1** (stale_running") < text.index("# ddflow brief"), text
    assert text.index(RESUME) < text.index("# ddflow brief"), text
    assert text.index("**T1** (stale_running") < text.index("## Suggested next"), text
    assert "**T1** (stale_running" in text, text


def test_the_brief_itself_puts_recoverable_work_before_new_work(repo):
    _seed(repo, ["T1", "T9"])
    EventLog(repo, "c").append("item.started", "T1", {})
    text = LC.brief(repo, item="T9", check_recovery=True, agent="next").data["text"]
    first = re.search(r"^## ", text, re.M)
    assert first is not None and text[first.start() :].startswith(RESUME), text
    # The recoverable ENTRY is inside the RESUME block and precedes the suggested work,
    # not only its heading (duck #6/#7).
    assert text.index(RESUME) < text.index("**T1** (stale_running"), text
    assert text.index("**T1** (stale_running") < text.index("## Current: T9"), text


def test_under_resume_keeps_the_choices_block_when_there_is_no_h1_heading():
    """A brief whose text has no `# ddflow brief` H1 line (empty or RESUME-only) must still
    carry the Open-workflow-choices block: the old prepend always emitted it, and hoisting it
    under the RESUME slice must not DROP it when there is no H1 to anchor under."""
    from ddflow.api.lifecycle.brief import _under_resume

    text = _under_resume("", "", "", "## Open workflow choices\n- pick A")
    assert text == "## Open workflow choices\n- pick A\n", text


def test_under_resume_anchors_to_the_last_exact_heading_line():
    """A RESUME-quoted or fenced `# ddflow brief` line must not steal the anchor: the choices
    block lands before the view's own H1 (the LAST exact `# ddflow brief` line), keeping
    recoverable work above it, not a fenced heading eaten by a code block."""
    from ddflow.api.lifecycle.brief import _under_resume

    text = "## Resume\n```text\n# ddflow brief\n```\n# ddflow brief\n## Work\n"
    out = _under_resume(text, "", "", "## Open workflow choices\n- pick A")
    # RESUME opens the pack, the fenced `# ddflow brief` stays inside its code block, and the
    # choices block lands between it and the view's own H1 (the last exact `# ddflow brief`).
    assert out.startswith("## Resume"), out
    assert out.index("```text\n# ddflow brief\n```") < out.index("## Open workflow choices"), out
    assert out.index("## Open workflow choices") < out.rindex("# ddflow brief"), out


def test_under_resume_ignores_a_fenced_heading_below_the_h1():
    """A `# ddflow brief` line inside a Work-note code block AFTER the view's H1 must not
    steal the anchor: `rfind` on the raw line kept the last match (the fenced one) and
    spliced the choices block into the code block, hiding it (grok/deepseek critic)."""
    from ddflow.api.lifecycle.brief import _under_resume

    text = "## Resume\n```text\n# ddflow brief\n```\n# ddflow brief\n## Work\n```text\n# ddflow brief\n```\n"
    out = _under_resume(text, "", "", "## Open workflow choices\n- pick A")
    # The choices block lands before the view's H1 (the last heading line OUTSIDE a fence),
    # and BOTH fenced copies stay inside their code blocks.
    assert out.index("## Resume") < out.index("## Open workflow choices"), out
    assert out.index("## Open workflow choices") < out.rindex("## Work"), out
    fence = "```text\n# ddflow brief\n```"
    assert out.count(fence) == 2, out
    # The Work code block is still intact and the choices block precedes it, not inside it.
    choices = out.index("## Open workflow choices")
    work = out.index("## Work")
    assert choices < work, out
    assert out[work:] == "## Work\n```text\n# ddflow brief\n```\n", out


def test_under_resume_ignores_tilde_and_longer_fences_below_the_h1():
    """``` and `````` fences all delimit, and a shorter fence line inside a longer fence is
    content, so a `# ddflow brief` inside any of them must not steal the anchor
    (grok/deepseek critic on the fence toggle)."""
    from ddflow.api.lifecycle.brief import _under_resume

    lead = "## Open workflow choices\n- pick A"
    # A tilde-fenced heading below the H1.
    tilde = "## Resume\n# ddflow brief\n## Work\n~~~text\n# ddflow brief\n~~~\n"
    out = _under_resume(tilde, "", "", lead)
    assert out.index("## Open workflow choices") < out.rindex("## Work"), out
    assert out[out.index("## Work") :] == "## Work\n~~~text\n# ddflow brief\n~~~\n", out
    # A longer fence containing a shorter same-char line keeps that line inside the fence.
    long_fence = "## Resume\n# ddflow brief\n## Work\n````markdown\n```\n# ddflow brief\n````\n"
    out = _under_resume(long_fence, "", "", lead)
    assert out.index("## Open workflow choices") < out.rindex("## Work"), out
    assert out[out.index("## Work") :] == "## Work\n````markdown\n```\n# ddflow brief\n````\n", out
    # A four-space-indented ``` line is an indented code block, not a fence close.
    indented = "# ddflow brief\n## Work\n```text\n    ```\n# ddflow brief\n```\n"
    out = _under_resume(indented, "", "", lead)
    assert out.index("## Open workflow choices") < out.rindex("## Work"), out
    assert out[out.index("## Work") :] == "## Work\n```text\n    ```\n# ddflow brief\n```\n", out
    # A tilde fence may carry any info string, even one with a tilde (only a backtick
    # opener's info string forbids a backtick); it still delimits.
    tilde_info = "# ddflow brief\n## Work\n~~~text has~tilde\n# ddflow brief\n~~~\n"
    out = _under_resume(tilde_info, "", "", lead)
    assert out.index("## Open workflow choices") < out.rindex("## Work"), out
    assert out[out.index("## Work") :] == "## Work\n~~~text has~tilde\n# ddflow brief\n~~~\n", out
