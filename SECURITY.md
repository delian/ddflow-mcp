# Security Policy

<!-- ddflow:begin security/versions sha=89892fe72efd -->
## Supported Versions

| Version | Status | Latest release | Known vulnerabilities |
| --- | --- | --- | --- |
| 0.2.x | Supported | 0.2.9 (2026-10-24) | vulnerable: B-mcp-no-type-enum-check, not fixed yet; vulnerable: B-memory-rerecord-keeps-old-text-in-log, fixed in the next release; vulnerable: B-token-role-widened-by-project-config, fixed in the next release; vulnerable: B18cd856a67, fixed in the next release; vulnerable: B58e9979b40, fixed in the next release; vulnerable: B5dc280b4eb, fixed in the next release; vulnerable: B6307b787dd, fixed in the next release; vulnerable: Bsecbug-redact-google-api-key, fixed in the next release |
| 0.1.x | Unsupported | 0.1.18 (2026-10-06) | none recorded |

A release is marked when a recorded security issue was found on or before its tag and the
fix is not in it. The newest release is also marked for any issue no release has fixed yet, even
one found after it was tagged. A line last tagged before an issue was found is not marked: that
says nothing is known, not that it is safe.
<!-- ddflow:end security/versions -->

<!-- ddflow:begin security/known-issues sha=86933ddc2cfa -->
## Known Issues

### Open

- B-mcp-no-type-enum-check (medium, ddflow), found 2026-10-22

### Fixed

- B-token-role-widened-by-project-config (high), fixed in the next release
- B6307b787dd (medium, ddflow), fixed in the next release
- B18cd856a67 (high, ddflow), fixed in the next release
- B5dc280b4eb (high, ddflow), fixed in the next release
- B58e9979b40 (high, ddflow), fixed in the next release (by date)
- Bsecbug-redact-google-api-key (high, ddflow), fixed in the next release
- B-memory-rerecord-keeps-old-text-in-log (low, ddflow), fixed in the next release
- Ba2df74d558 (high), fixed in 0.2.8
- B-pin-identity-leak (medium, ddflow), fixed in 0.2.1 (by date)
- B5deba76d04 (high), fixed in 0.2.1
- B9fad3e83d9 (medium), fixed in 0.2.1
- Bb2c7cdd441 (medium, ddflow), fixed in 0.1.16

Source: the bugs recorded as security issues in this project's log. Dependency advisories and
security-pass findings are not recorded there, so they are not covered here.
<!-- ddflow:end security/known-issues -->

<!-- ddflow:begin security/reporting sha=4022b164ce84 -->
## Reporting a Vulnerability

TODO (project owner): no reporting contact is set for this project, so a reporter has nowhere
private to go. Set one with `ddflow config --set export.security_contact "<address or URL>"`
(ddflow never invents a contact), then run `ddflow export security --update`.
<!-- ddflow:end security/reporting -->
