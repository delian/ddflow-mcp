"""SEC-policy-contact: the security document carries a configured private reporting contact.

``[export].security_contact`` (default empty) and ``[export].security_response_days`` (default 0,
not stated) fill the "Reporting a Vulnerability" section, a named region of SECURITY.md. With no
contact the section says plainly that none is set, ddflow never invents one, and ``doctor`` notes
it. The repository's own SECURITY.md is that generated document, not the GitHub template.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import reporting
from ddflow.config import Config
from ddflow.services.export import Filters, ops, query, registry
from ddflow.services.export import regions as RG

REPO = Path(__file__).resolve().parent.parent
CONTACT = "security@corp.example"
NOTE = "SECURITY.md has no reporting contact"


def _body(repo: Path, toml: str = "") -> str:
    """The security region bodies as the export renders them, after redaction."""
    p = repo / ".ddflow" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(toml)
    cfg = Config.load(repo)
    q = query.build([])
    q.repo = repo
    spec = ops.spec_for(cfg, "security", writing=True)
    ops.write_doc(repo, cfg, q, spec)
    return (repo / "SECURITY.md").read_text()


def _reporting(text: str) -> str:
    (r,) = [r for r in RG.parse(text) if r.name == "reporting"]
    return text[r.begin : r.stop]


def test_a_configured_contact_fills_the_reporting_region_with_no_todo(repo: Path) -> None:
    text = _body(repo, f'[export]\nsecurity_contact = "{CONTACT}"\nsecurity_response_days = 5\n')
    section = _reporting(text)
    assert CONTACT in section and "TODO" not in text
    assert "within 5 days" in section
    assert "## Reporting a Vulnerability" in section
    assert "not set" not in section


def test_the_contact_is_not_redacted_as_an_email_address(repo: Path) -> None:
    # the export redacts emails by default; the operator's own published contact is the exception
    text = _body(repo, f'[export]\nsecurity_contact = "{CONTACT}"\n')
    assert "[REDACTED" not in text and CONTACT in text


def test_a_link_contact_is_written_as_given(repo: Path) -> None:
    link = "https://github.com/acme/proj/security/advisories/new"
    assert link in _body(repo, f'[export]\nsecurity_contact = "{link}"\n')


def test_without_a_contact_the_section_says_none_is_set_and_never_invents_one(repo: Path) -> None:
    text = _body(repo)
    section = _reporting(text)
    assert "TODO" in section and "no reporting contact is set" in section
    assert "export.security_contact" in section
    assert "@" not in section and "within" not in section  # nothing stated that is not known


def test_the_reporting_region_is_refreshed_when_the_contact_is_set_later(repo: Path) -> None:
    assert "TODO" in _body(repo)
    text = _body(repo, f'[export]\nsecurity_contact = "{CONTACT}"\n')
    assert CONTACT in text and "TODO" not in text and text.count("Reporting a Vulnerability") == 1


def test_the_text_outside_the_regions_is_still_the_operators(repo: Path) -> None:
    _body(repo)
    p = repo / "SECURITY.md"
    p.write_text("Intro by hand.\n\n" + p.read_text() + "\nOutro by hand.\n")
    _body(repo, f'[export]\nsecurity_contact = "{CONTACT}"\n')
    text = p.read_text()
    assert text.startswith("Intro by hand.\n\n") and text.endswith("\nOutro by hand.\n")


def test_the_knobs_default_to_empty_and_not_stated() -> None:
    cfg = Config()
    assert cfg.export.security_contact == "" and cfg.export.security_response_days == 0


def test_a_negative_response_time_is_refused(repo: Path) -> None:
    code, _out, err = run_cli(repo, "config", "--set", "export.security_response_days", "-1")
    assert code == 3 and "export.security_response_days" in err


def test_the_cli_sets_the_contact_and_the_next_render_has_it(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == 0
    code, _out, err = run_cli(repo, "config", "--set", "export.security_contact", CONTACT)
    assert code == 0, err
    assert Config.load(repo).export.security_contact == CONTACT
    q = query.build([])
    q.repo = repo
    assert CONTACT in registry.render_body("security", q, Filters())


def _doctor_notes(repo: Path) -> list[str]:
    return list(reporting.doctor(repo, agent="a1").data["notes"])


def _selected(repo: Path, extra: str = "") -> None:
    assert run_cli(repo, "init")[0] == 0
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + f'\n[export]\ndocuments = ["security"]\n{extra}')


def test_doctor_notes_a_selected_security_document_with_no_contact(repo: Path) -> None:
    _selected(repo)
    assert any(NOTE in n for n in _doctor_notes(repo))
    assert any("export.security_contact" in n for n in _doctor_notes(repo))


def test_doctor_is_quiet_once_the_contact_is_set(repo: Path) -> None:
    _selected(repo, f'security_contact = "{CONTACT}"\n')
    assert not any(NOTE in n for n in _doctor_notes(repo))


def test_doctor_says_nothing_when_the_security_document_is_not_selected(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == 0
    assert not any(NOTE in n for n in _doctor_notes(repo))


def test_this_repositorys_security_md_is_the_generated_document_not_the_template() -> None:
    text = (REPO / "SECURITY.md").read_text()
    assert "5.1.x" not in text and "Use this section" not in text
    assert {r.name for r in RG.parse(text)} >= {"versions", "known-issues", "reporting"}
    assert "## Reporting a Vulnerability" in text


@pytest.mark.parametrize("bad", ["", "  "])
def test_a_blank_contact_counts_as_unset(repo: Path, bad: str) -> None:
    assert "no reporting contact is set" in _body(repo, f'[export]\nsecurity_contact = "{bad}"\n')


def test_a_long_contact_is_published_whole(repo: Path) -> None:
    link = "https://example.com/report?token=" + "a" * 400
    assert link in _body(repo, f'[export]\nsecurity_contact = "{link}"\n')


def test_the_contact_exemption_does_not_unmask_other_addresses_that_contain_it(
    repo: Path,
) -> None:
    from ddflow.services.export import safe

    cfg = Config()
    cfg.export.security_contact = "example.com"
    red = safe.redact_text("write to alice@example.com or example.com.", cfg, exempt_contact=True)
    assert "alice" not in red.text and "[REDACTED:email]" in red.text
    assert red.text.endswith("or example.com.")
    cfg.export.security_contact = "sec@corp.example"
    assert (
        "xsec@corp.example"
        not in safe.redact_text("xsec@corp.example", cfg, exempt_contact=True).text
    )
    assert (
        safe.redact_text("<sec@corp.example>.", cfg, exempt_contact=True).text
        == "<sec@corp.example>."
    )


def test_a_contact_that_is_a_local_part_does_not_unmask_the_address_it_starts(repo: Path) -> None:
    from ddflow.services.export import safe

    cfg = Config()
    cfg.export.security_contact = "alice"
    assert (
        "alice" not in safe.redact_text("write to alice@example.com", cfg, exempt_contact=True).text
    )


def test_a_contact_with_inner_whitespace_is_published_as_the_document_writes_it(repo: Path) -> None:
    contact = "mail  security@corp.example\nor call the desk"
    text = _body(repo, f'[export]\nsecurity_contact = """{contact}"""\n')
    assert "mail security@corp.example or call the desk" in text and "[REDACTED" not in text


def test_outside_the_export_the_contact_is_still_redacted() -> None:
    from ddflow.services.export import safe

    cfg = Config()
    cfg.export.security_contact = "sec@corp.example"
    assert safe.redact_text("mail sec@corp.example", cfg).text == "mail [REDACTED:email]"


def test_another_document_still_redacts_the_contact(repo: Path) -> None:
    cfg = Config()
    cfg.export.security_contact = "sec@corp.example"
    q = query.build([])
    q.repo = repo
    other = ops.spec_for(cfg, "status", writing=False)
    body = ops._post(cfg, repo, other)("mail sec@corp.example")[0]
    assert body == "mail [REDACTED:email]"
    sec = ops.spec_for(cfg, "security", writing=False)
    assert ops._post(cfg, repo, sec)("mail sec@corp.example")[0] == "mail sec@corp.example"
