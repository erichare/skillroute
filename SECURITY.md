# Security Policy

## Supported Versions

Only the latest release receives security fixes.

## Reporting a Vulnerability

Please report vulnerabilities privately via
[GitHub security advisories](https://github.com/jestatsio/skillroute/security/advisories/new)
rather than opening a public issue. You should receive a response within a week.

## Scope notes

- The Skill Atlas UI server (`skillroute ui`) binds to `127.0.0.1` by default
  and is intended for local use; do not expose it to untrusted networks.
- The MCP server executes the SkillRoute CLI locally and inherits the
  permissions of the user running it.
