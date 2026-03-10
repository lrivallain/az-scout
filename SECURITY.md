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
2. Use [GitHub's private vulnerability reporting](https://github.com/az-scout/az-scout/security/advisories/new).
3. Include a clear description of the vulnerability, steps to reproduce, and any potential impact.

You can expect an initial response within **72 hours**. Once the issue is confirmed, a fix will be developed and released as soon as possible.

## Scope

This tool runs locally and calls the Azure Resource Manager REST API using your own credentials. It does **not** store or transmit credentials outside of the Azure SDK's `DefaultAzureCredential` flow. Relevant concerns include:

- Accidental exposure of subscription data in exported files (PNG/CSV).
- Dependencies with known vulnerabilities.
- Any way to bypass Azure RBAC through the tool.

### Plugin security

Plugins run in-process with the main application and have access to the same resources and permissions as the core code. This means that a malicious or vulnerable plugin could potentially:
- Access Azure credentials via the same SDK calls as the core code.
- Read/write any files the application can access (including exported data).
- Make arbitrary HTTP requests from the user's machine.
For this reason, **only install plugins from trusted sources**.

**Plugin responsibility**: Plugin authors should follow secure coding practices, avoid hardcoding secrets, and be transparent about the data their plugin accesses and transmits. Users should review plugin source code and permissions before installation. Az-scout team is not responsible for third-party plugins.
