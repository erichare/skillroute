# SkillRoute MCP Server by JEStats

Stdio MCP server exposing [SkillRoute](https://github.com/jestatsio/skillroute) routing tools to agent clients: `skillroute.route`, `skillroute.search`, and `skillroute.inspect_skill`.

Requires Node.js 20+ and [uv](https://docs.astral.sh/uv/), or an installed Python `skillroute` command (`uv tool install skillroute` / `pipx install skillroute`). With uv on PATH, the server downloads the Python core on demand. No repository build or JEStats account is needed.

Configure your MCP client to run:

```bash
npx -y @skillroute/mcp-server
```

Index your library once before asking the agent to route:

```bash
uvx skillroute dogfood index
# Or: uvx skillroute index --root ./skills
```

In a SkillRoute source checkout the server autodetects the repo and runs against the checkout instead. Environment overrides:

- `SKILLROUTE_REPO_ROOT` — force a specific checkout
- `SKILLROUTE_PYTHON` — interpreter to run `-m skillroute` with
- `SKILLROUTE_BRIDGE_TIMEOUT_MS` — bridge call timeout (default 30000)

See the [agent setup guide](https://github.com/jestatsio/skillroute/blob/main/docs/agent-setup.md) for Claude Code/Codex plugins and `skillroute harness install <agent>` for all 15 clients. Built by [JEStats](https://jestats.io).
