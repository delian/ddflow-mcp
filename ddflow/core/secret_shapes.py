"""The shapes of provider credentials, applied to every text written to the committed log.

The session's own `redact_patterns` are a user-editable list; replacing it must not drop
these, so they are a separate table, on by default (`[log].redact_builtin_shapes`) and
joined to the session patterns in ONE place (`session_secret_patterns`), which the log
writer, the hooks and the report path all call.

The table agrees with the gitleaks default rules that matter here (`.gitleaks.toml` extends
them): a prefix that names the provider, then a run of the provider's key alphabet. Every
pattern is a literal prefix followed by one bounded character class, so a match is linear
(no nested quantifier, nothing to backtrack into). The short, common prefixes (`sk-`, `hf_`,
`xai-`, `AKIA`...) carry a left guard so they do not fire inside a longer alphanumeric word;
the long distinctive ones (`AIza`, `sk-ant-`, `ghp_`, `github_pat_`...) do not, since a key
pasted against other text (`keyAIza...`) must still go.

The generic `key = value` shape needs a letter AND a digit within the first 20 characters of a value of 20 or
more, so `token = 5` and prose about tokens are left alone, and a JSON-quoted key is caught.
"""

from __future__ import annotations

import re

_L = r"(?<![0-9A-Za-z])"  # not inside a longer alphanumeric word

#: (name, regex). The name is for tests and `ddflow doctor`; the text never carries it.
SHAPES: tuple[tuple[str, str], ...] = (
    # the newer format: `AQ.` then exactly 50 letters/digits (53 total); no guards, listed FIRST so a legacy key glued before it cannot eat its `AQ`, so a key glued to text still goes
    ("google-api-key", r"AQ\.[0-9A-Za-z]{50}"),
    ("google-api-key", r"AIza[0-9A-Za-z_-]{35,}"),
    ("google-oauth", r"ya29\.[0-9A-Za-z_-]{30,}"),
    ("anthropic", r"sk-ant-[0-9A-Za-z_-]{20,}"),
    ("openai-project", r"sk-(?:proj|svcacct|admin)-(?=[A-Za-z_-]{0,60}[0-9])[0-9A-Za-z_-]{40,}"),
    ("openai", r"sk-(?=[A-Za-z]{0,60}[0-9])(?=[0-9]{0,60}[A-Za-z])[0-9A-Za-z]{32,}"),
    ("xai", _L + r"xai-[0-9A-Za-z]{20,}"),
    ("groq", _L + r"gsk_[0-9A-Za-z]{20,}"),
    ("github", r"gh[pousr]_[0-9A-Za-z]{20,}"),
    ("github-fine-grained", r"github_pat_[0-9A-Za-z_]{22,}"),
    (
        "aws-access-key-id",
        r"(?<![A-Z])(?:AKIA|ASIA|ABIA|ACCA)(?![0-9A-Z]{0,12}EXAMPLE)[0-9A-Z]{16}",
    ),
    ("slack", r"xox[abposr]-[0-9A-Za-z-]{10,}"),
    ("slack-app", r"xapp-[0-9]-[0-9A-Za-z-]{10,}"),
    ("stripe", r"[sr]k_live_(?=[A-Za-z]{0,60}[0-9])[0-9A-Za-z]{24,}"),
    ("huggingface", _L + r"hf_[0-9A-Za-z]{30,}"),
    ("npm", _L + r"npm_[0-9A-Za-z]{36}(?![0-9A-Za-z])"),
    ("gitlab", r"glpat-[0-9A-Za-z_-]{20,}"),
    ("pypi", r"pypi-AgEI[0-9A-Za-z_-]{50,}"),
    ("sendgrid", r"SG\.[0-9A-Za-z_-]{22}\.[0-9A-Za-z_-]{43}"),
    ("linear", r"lin_api_[0-9A-Za-z]{30,}"),
    ("digitalocean", r"do[por]_v1_[0-9a-f]{64}"),
    ("mailgun", _L + r"key-[0-9a-f]{32}(?![0-9A-Za-z])"),
    # three dot-separated base64url parts (RFC 7515 forbids padding, and a `=` would make the masking style keep the head); the header and the payload are JSON, so both start `eyJ`
    ("jwt", r"eyJ[0-9A-Za-z_-]{8,}\.eyJ[0-9A-Za-z_-]{8,}\.[0-9A-Za-z_-]*"),
    # a non-conforming padded payload: the header and payload, up to the padding; the masking style keeps
    # whatever precedes the first `=` of a match, so none may sit inside it (the signature alone is no token)
    ("jwt-padded", r"eyJ[0-9A-Za-z_-]{8,}\.eyJ[0-9A-Za-z_-]{8,}(?==+\.)"),
    ("azure-key", r"(?:Account|SharedAccess)Key=[0-9A-Za-z+/]{40,}={0,2}"),
    # `curl ... -u user:password`: scoped to curl, since `docker run -u 1000:1000` is not a credential
    (
        "curl-user",
        r"\bcurl\b(?:[^\n|;&]|&(?!&)|\\\n){0,400}?\s(?:-[A-Za-z]*u|--user)[ =]*(?:\"[^\"\n:]{1,64}:[^\"\n]{4,256}|'[^'\n:]{1,64}:[^'\n]{4,256}|[^\s:'\"]{1,64}:[^\s'\"]{4,256})",
    ),
    # `scheme://user:password@host`: the whole password, which may itself hold an `@`
    (
        "url-userinfo",
        r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{1,20}://[^\s:/@]{0,64}:[^\s/]{1,256}@",
    ),
    (
        "assigned-secret",
        r"(?i)(?:api[-_]?key|(?:access|auth|private)[-_]?key|(?<![A-Za-z0-9])key|secret|token|passw(?:or)?d)[\w-]{0,40}[\"']?\s*[:=]\s*[\"']?"
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
