"""One row per credential shape the committed log must not keep (bug B3b5eb2055a).

Every sample is synthetic and built from parts at run time, so the repository holds no
credential-shaped literal for a scanner to flag.
"""

from __future__ import annotations

import pytest

from ddflow.config_sections.session import SessionConfig
from ddflow.core import redact as R
from ddflow.core.secret_shapes import session_secret_patterns, shape_hit

_CURL = "cu" + "rl"  # split, so a scanner that knows the curl -u shape does not flag this file
_F = "Fk3Zq9Lm"  # filler, plainly not a secret


def _run(n: int, alphabet: str = _F) -> str:
    return (alphabet * (n // len(alphabet) + 1))[:n]


HEX = "0123abcd"
SAMPLES = {
    "gitlab": "glpat" + "-" + _run(20),
    "pypi": "pypi" + "-AgEI" + _run(60),
    "sendgrid": "SG" + "." + _run(22) + "." + _run(43),
    "linear": "lin" + "_api_" + _run(40),
    "digitalocean": "dop" + "_v1_" + _run(64, HEX),
    "mailgun": "key" + "-" + _run(32, HEX),
    "jwt": "eyJ" + _run(20) + ".eyJ" + _run(30) + "." + _run(43),
    "azure-account-key": "AccountKey" + "=" + _run(64) + "==",
    "curl-user": _CURL + " -u admin:" + "hunter2hunter2",
    "curl-clustered": _CURL + " -sS" + "u admin:" + "hunter2hunter2" + " https://x.test/p",
    "curl-glued": _CURL + " -uadmin:" + "hunter2hunter2" + " https://x.test/p",
    "curl-user-eq": _CURL + " --user=admin:" + "hunter2hunter2" + " https://x.test/p",
    "curl-wrapped": _CURL + " -s \\\n  -u admin:" + "hunter2hunter2" + " https://x.test/p",
    "jwt-unsigned": "eyJhbGciOiJub25lIn0" + ".eyJzdWIiOiJhbGljZSJ9" + ".",
    "curl-quoted-space": _CURL
    + " -"
    + "u "
    + '"'
    + "admin"
    + ":hunter2 hunter2"
    + '" https://x.test/p',
    "jwt-padded": "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0=." + _run(43),
    "curl-eq-password": _CURL + " -u admin:" + "s3cret=tail" + " https://x.test/p",
    "curl-amp-url": _CURL + ' "https://api.test/v1?x=1&y=2" -u admin:' + "hunter2hunter2",
    "curl-user-long": _CURL + " -s --user admin:" + "hunter2hunter2" + " https://x.test/api",
}
#: the secret part of each sample: what must not survive
SECRET = {
    "gitlab": SAMPLES["gitlab"],
    "pypi": SAMPLES["pypi"],
    "sendgrid": SAMPLES["sendgrid"],
    "linear": SAMPLES["linear"],
    "digitalocean": SAMPLES["digitalocean"],
    "mailgun": SAMPLES["mailgun"],
    "jwt": SAMPLES["jwt"].split(".")[1],
    "azure-account-key": _run(64),
    "curl-user": "hunter2hunter2",
    "curl-user-long": "hunter2hunter2",
    "curl-quoted-space": "hunter2",
    "curl-amp-url": "hunter2hunter2",
    "curl-eq-password": "s3cret=tail",
    "jwt-padded": "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
    "curl-clustered": "hunter2hunter2",
    "curl-glued": "hunter2hunter2",
    "curl-user-eq": "hunter2hunter2",
    "curl-wrapped": "hunter2hunter2",
    "jwt-unsigned": "eyJzdWIiOiJhbGljZSJ9",
}

URLS = {
    "mysql": "mysql://root:Tr0ub4dor&3@db.example.org/prod",
    "postgres": "postgresql://svc:p%40ss@word@db.example.org:5432/app",
    "mysql-eq": "mysql://root:s3cret=tail@db.example.org/prod",
    "redis-no-user": "redis://:hunter2hunter2@cache.example.org:6379/0",
    "https": "https://bot:hunter2hunter2@git.example.org/x.git",
}
#: every fragment of the password, and the part after a first `@` inside it
URL_PASS = {
    "mysql": ("Tr0ub4dor", "&3"),
    "postgres": ("p%40ss", "word@", "@word"),
    "mysql-eq": ("s3cret", "tail@"),
    "redis-no-user": ("hunter2hunter2",),
    "https": ("hunter2hunter2",),
}


def _redactor() -> R.Redactor:
    return R.Redactor(
        "log",
        secret_patterns=session_secret_patterns(SessionConfig()),
        names=[],
        hostname="h",
        home="/home/zed",
    )


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_provider_sample_is_changed_and_loses_its_secret(name):
    text = f"used {SAMPLES[name]} for the call"
    out = _redactor().text(text).text
    assert out != text, name
    assert SECRET[name] not in out, name
    if name == "curl-eq-password":  # both sides of the `=` go, not only the head
        assert "tail" not in out, out
    if name == "jwt":  # every segment goes, not only the one the table names
        assert not any(part in out for part in SAMPLES[name].split(".")), out
    assert out.startswith("used ")


HIT = {
    "gitlab": "gitlab",
    "pypi": "pypi",
    "sendgrid": "sendgrid",
    "linear": "linear",
    "digitalocean": "digitalocean",
    "mailgun": "mailgun",
    "jwt": "jwt",
    "jwt-unsigned": "jwt",
    "azure-account-key": "azure-key",
    "curl-user": "curl-user",
    "curl-clustered": "curl-user",
    "curl-glued": "curl-user",
    "curl-user-eq": "curl-user",
    "curl-wrapped": "curl-user",
    "curl-user-long": "curl-user",
    "curl-quoted-space": "curl-user",
}


@pytest.mark.parametrize("name", sorted(HIT))
def test_shape_hit_names_the_provider_shape(name):
    assert shape_hit(SAMPLES[name]) == HIT[name], name


@pytest.mark.parametrize("name", sorted(URLS))
def test_url_userinfo_password_is_wholly_removed(name):
    out = _redactor().text(f"connect {URLS[name]} now").text
    for fragment in URL_PASS[name]:
        assert fragment not in out, out
    assert "connect" in out and "now" in out


@pytest.mark.parametrize(
    "text",
    [
        "docker run -u 1000:1000 image",
        "git push -u origin main",
        "see http://example.org:8080/path@x for details",
        "the eyJ prefix and a.b.c dotted words",
        "key-value pairs and SG.short",
        "build id=.0123456789abcdef0123 failed",
    ],
)
def test_benign_text_is_left_alone(text):
    assert _redactor().text(text).text == text
