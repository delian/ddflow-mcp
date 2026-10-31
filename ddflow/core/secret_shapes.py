"""The shapes of provider credentials, applied to every text written to the committed log.

The session's own `redact_patterns` are a user-editable list; replacing it must not drop
these, so they are a separate table, on by default (`[log].redact_builtin_shapes`) and
joined to the session patterns in ONE place (`session_secret_patterns`), which the log
writer, the hooks and the report path all call.

The table agrees with the gitleaks default rules that matter here (`.gitleaks.toml` extends
them): a prefix that names the provider, then a run of the provider's key alphabet. Every
pattern is a literal prefix followed by one bounded character class, so a match is linear
(no nested quantifier, nothing to backtrack into). A left guard keeps a shape from firing
inside a longer alphanumeric word (a base64 blob that happens to contain `AIza`).

The generic `key = value` shape needs a letter AND a digit within the first 20 characters of a value of 20 or
more, so `token = 5` and prose about tokens are left alone, and a JSON-quoted key is caught.
"""

from __future__ import annotations

import re

_L = r"(?<![0-9A-Za-z])"  # not inside a longer alphanumeric word

#: (name, regex). The name is for tests and `ddflow doctor`; the text never carries it.
SHAPES: tuple[tuple[str, str], ...] = (
    ("google-api-key", _L + r"AIza[0-9A-Za-z_-]{35,}"),
    ("google-oauth", _L + r"ya29\.[0-9A-Za-z_-]{30,}"),
    ("anthropic", _L + r"sk-ant-[0-9A-Za-z_-]{20,}"),
    ("openai", _L + r"sk-(?:proj-|svcacct-|admin-)?[0-9A-Za-z_-]{20,}"),
    ("xai", _L + r"xai-[0-9A-Za-z]{20,}"),
    ("groq", _L + r"gsk_[0-9A-Za-z]{20,}"),
    ("github", _L + r"gh[pousr]_[0-9A-Za-z]{20,}"),
    ("github-fine-grained", _L + r"github_pat_[0-9A-Za-z_]{22,}"),
    ("aws-access-key-id", _L + r"(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}(?![0-9A-Z])"),
    ("slack", _L + r"xox[abposr]-[0-9A-Za-z-]{10,}"),
    ("slack-app", _L + r"xapp-[0-9]-[0-9A-Za-z-]{10,}"),
    ("stripe", _L + r"[sr]k_live_[0-9A-Za-z]{16,}"),
    ("huggingface", _L + r"hf_[0-9A-Za-z]{30,}"),
    ("npm", _L + r"npm_[0-9A-Za-z]{36}(?![0-9A-Za-z])"),
    (
        "assigned-secret",
        r"(?i)(?:api[-_]?key|secret|token|passw(?:or)?d)[\w-]{0,40}[\"']?\s*[:=]\s*[\"']?"
        r"(?=[0-9A-Za-z_/+.=-]{20})(?=[A-Za-z_/+.=-]{0,19}[0-9])(?=[0-9_/+.=-]{0,19}[A-Za-z])"
        r"[0-9A-Za-z_/+.=-]{20,512}",
    ),
)


def shape_patterns() -> list[str]:
    """The regexes of `SHAPES`, in order."""
    return [rx for _name, rx in SHAPES]


def session_secret_patterns(session: object, builtin: bool = True) -> list[str]:
    """The patterns every text writer applies: the session's `redact_patterns` and
    `redact_extra`, plus the built-in shapes unless ``builtin`` is off
    (`[log].redact_builtin_shapes`)."""
    patterns: list[str] = [
        *(getattr(session, "redact_patterns", None) or ()),
        *(getattr(session, "redact_extra", None) or ()),
    ]
    if builtin:
        patterns = [*shape_patterns(), *patterns]
    return patterns


_COMPILED = [(name, re.compile(rx)) for name, rx in SHAPES]
GENERIC = "assigned-secret"


def shape_hit(text: str, *, generic: bool = False) -> str:
    """The name of the first provider shape ``text`` matches, or "" (never the value).

    The generic `key = value` shape is off by default: it is a redaction heuristic, too
    loose to refuse a commit on."""
    for name, rx in _COMPILED:
        if (generic or name != GENERIC) and rx.search(text) is not None:
            return name
    return ""
