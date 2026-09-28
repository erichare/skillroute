# Contributing

Thanks for your interest in SkillRoute! This page covers local development and
the release process.

## Development setup

Requirements: [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
# Python package (src/skillroute) + tests
uv sync --extra dev
uv run --extra dev pytest --cov=skillroute --cov-fail-under=80
uv run --extra dev ruff check .
uv run --extra dev mypy

# Skill Atlas web UI
npm --prefix web ci
npm --prefix web run typecheck && npm --prefix web run lint && npm --prefix web run test

# MCP server
npm --prefix mcp ci
npm --prefix mcp run build && npm --prefix mcp test && npm --prefix mcp run smoke
```

CI mirrors these commands plus dependency audits and a packaging check; see
[.github/workflows/ci.yml](.github/workflows/ci.yml).

## Guidelines

- Conventional commit messages (`feat:`, `fix:`, `chore:`, ...)
- Add or update tests with behavior changes; overall coverage must stay >= 80%
- `ruff` and `mypy` must pass

## Releasing

1. Bump the version in `pyproject.toml`, `mcp/package.json` (and
   `web/package.json` for consistency). Python code and the MCP server read
   their versions from package metadata — do not hardcode versions elsewhere.
   Also bump `dsh-plugin/package.json`, and in
   `claude-plugin/.claude-plugin/plugin.json` both `version` and the
   `@skillroute/mcp-server@X.Y.Z` pin. The release workflow and
   `tests/test_claude_plugin.py` fail if any of them disagree.
2. Add a section to `CHANGELOG.md` and update its compare/tag links.
3. Land those changes on `main`, then tag and push:

   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

4. The [release workflow](.github/workflows/release.yml) builds the sdist,
   wheel, and npm tarball, then creates a GitHub release with the artifacts
   attached.
   - PyPI publishing runs only when the `PYPI_PUBLISH` repository variable is
     `true` and a [PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/)
     is configured for this repository.
   - npm publishing runs only when the `NPM_PUBLISH` repository variable is
     `true` and an `NPM_TOKEN` secret with publish rights to the
     `@skillroute` scope is configured.
