# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| main    | Yes       |
| 0.1.x   | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability in orchcore, please report it responsibly.

Do not open a public issue.

If the repository exposes GitHub's "Report a vulnerability" flow, use the private advisory form:
[github.com/AbdelazizMoustafa10m/orchcore/security/advisories/new](https://github.com/AbdelazizMoustafa10m/orchcore/security/advisories/new).

If private reporting is not yet enabled, contact the maintainer privately using the contact details
listed on the repository owner profile instead of filing a public issue.

Reports will be acknowledged within 48 hours and receive a detailed follow-up within 7 days.

## Scope

orchcore is a library that launches subprocesses. Security-relevant areas include:

- Command injection via agent configuration or prompt templates
- Path traversal in workspace or prompt file loading
- Secrets leaking through logs or stream output

If you are unsure whether a finding qualifies as a vulnerability, report it anyway.
