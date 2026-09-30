# Getting Started

SkillRoute is a local CLI and MCP server for routing agent requests to the right
skills.

## Install

One-line SkillRoute installer:

```bash
curl -fsSL https://raw.githubusercontent.com/jestatsio/skillroute/main/scripts/install.sh | bash
```

The installer confirms each step before it clones or updates SkillRoute,
installs dependencies, builds the MCP server and Skill Atlas web UI, indexes
starter skills, detects supported agent clients, and offers setup for each
detected client. It installs to `~/.skillroute/skillroute` by default, reusing an existing `skill-route` checkout if one is present.

For unattended local/dev use:

```bash
curl -fsSL https://raw.githubusercontent.com/jestatsio/skillroute/main/scripts/install.sh | bash -s -- --yes
```

Already in a checkout:

```bash
./scripts/bootstrap.sh
```

That installs the Python environment, installs and builds the MCP server, indexes
the example skills, builds the Skill Atlas UI, and prints copy-paste setup
commands for supported clients.

Manual setup is also supported:

```bash
uv sync --extra dev   # dev includes the `ui` extra
npm --prefix mcp ci
npm --prefix mcp run build
npm --prefix web ci
npm --prefix web run build
```

The web build is required before `skillroute ui` can serve the Skill Atlas.

## Index Example Skills

```bash
uv run skillroute index --root examples/skills
```

This creates `~/.skillroute/catalog.db` unless a `.skillroute/catalog.db` already
exists in the current directory, in which case that one is used. Override with
`--catalog` or set `SKILLROUTE_CATALOG_PATH`.

Every bundle is checked against the [Agent Skills
specification](https://agentskills.io/specification) as it is indexed; run the
validator on its own for the full report, or pass `--strict` to refuse
non-compliant bundles:

```bash
uv run skillroute validate examples/skills
```

## Route A Request

```bash
uv run skillroute route "Build an MCP server that exposes routing tools"
```

The response includes ranked skills, confidence, reasons, evidence snippets,
score components, suggested order, and clarification questions when needed.

## Explore The Skill Atlas

```bash
uv run skillroute ui
```

Skill Atlas opens a local read-only graph for browsing domains, relationships,
evidence, backend refs, and route previews.

## Search And Inspect

```bash
uv run skillroute search "Astra vector backend"
uv run skillroute inspect astra-vector-backend
```

Search is useful for catalog exploration. Inspect shows reviewed metadata,
relationships, excerpts, source paths, and backend refs.

## Dogfood Local Skills

```bash
uv run skillroute dogfood roots
uv run skillroute dogfood index
uv run skillroute route "Review a GitHub PR with exact file and line evidence"
```

Dogfood discovery checks:

- `~/.codex/skills`
- `~/.agents/skills`
- `~/.codex/plugins/cache`

## Next

- Connect SkillRoute to an agent client with [agent setup](agent-setup.md).
- Explore the local graph with [Skill Atlas](skill-atlas.md).
- Configure [Astra retrieval](astra-backend.md) when you want remote vectorize
  search.
- Use [metadata overlays](metadata-overlays.md) to review inferred facets and
  relationships.
- Run [golden-route evals](evals.md) before changing router scoring.
