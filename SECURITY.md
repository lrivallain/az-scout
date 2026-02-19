# Security Policy

## Supported Versions

| Version   | Supported          |
|-----------|--------------------|
| Latest    | ✅                 |
| Older     | ❌                 |

Only the latest released version receives security updates.

## Reporting a Vulnerability

If you discover a security vulnerability in **az-scout**, please report it responsibly:

1. **Do not** open a public GitHub issue.
2. Email the maintainer at **security@yourproject.example** or use [GitHub's private vulnerability reporting](https://github.com/lrivallain/az-scout/security/advisories/new).
3. Include a clear description of the vulnerability, steps to reproduce, and any potential impact.

You can expect an initial response within **72 hours**. Once the issue is confirmed, a fix will be developed and released as soon as possible.

## Scope

This tool runs locally and calls the Azure Resource Manager REST API using your own credentials. It does **not** store or transmit credentials outside of the Azure SDK's `DefaultAzureCredential` flow. Relevant concerns include:

- Accidental exposure of subscription data in exported files (PNG/CSV).
- Dependencies with known vulnerabilities.
- Any way to bypass Azure RBAC through the tool.
