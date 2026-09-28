# SkillRoute for Claude Code

[SkillRoute](https://github.com/erichare/skillroute) is a local-first skill catalog and router. Index the SKILL.md bundles you already have once. When a task comes in, Claude asks SkillRoute which skills fit and gets a ranked shortlist with confidence scores, the reasons for each match, evidence from the skill text, and clarifying questions when the request is ambiguous. It's useful once your library has grown past what a model can reliably pick from by description alone.

## What's in the plugin

- **The SkillRoute MCP server** ([`@skillroute/mcp-server`](https://www.npmjs.com/package/@skillroute/mcp-server), pinned to an exact version) with three tools: `skillroute.route`, `skillroute.search`, and `skillroute.inspect_skill`.
- **The `skillroute` skill**, which tells Claude when to route, how to read the results, and how to build the catalog when it's empty.

## Requirements

- Node.js 20 or later, to run the MCP server with `npx`.
- The SkillRoute Python package, used by the server for indexing and ranking. Either install it (`pipx install skillroute`, `uv tool install skillroute`, or `brew install erichare/skillroute/skillroute`), or have [uv](https://docs.astral.sh/uv/) on your `PATH`. Without an installed `skillroute` command, the server runs it through `uvx --from skillroute`, which downloads the package from PyPI.

## Get started

Build the catalog from the skill directories you use. Each run adds to it:

```bash
uvx skillroute index --root ~/.claude/skills
```

Then ask Claude something like "Which of my skills should I use to add pytest coverage to this repo?" SkillRoute answers from `~/.skillroute/catalog.db`.

See the [SkillRoute README](https://github.com/erichare/skillroute#readme) for the CLI, the Skill Atlas web UI, retrieval backends, and route analytics.

## What it runs and sends

- **At startup:** Claude Code runs `npx -y @skillroute/mcp-server@<version>`, which downloads the server from npm. For each tool call, the server runs the `skillroute` CLI if it's installed, and otherwise `uvx --from skillroute skillroute`, which fetches the Python package from PyPI.
- **Local data:** it reads SKILL.md files when you run `skillroute index`. When Claude passes a `repo` to `skillroute.route`, SkillRoute lists that directory's file names to detect languages and project files such as `pyproject.toml`. The catalog and a trace of each route are stored in `~/.skillroute/catalog.db` on your machine.
- **Network:** the default `local` and `fts5` backends make no network requests. The optional `astra` backend, used only when you choose it, sends queries to your own Astra DB database using the `ASTRA_DB_*` credentials in your environment.

## Privacy

SkillRoute has no telemetry and no hosted service. The catalog, route traces, and analytics stay in `~/.skillroute/` on your machine. The only network traffic is the package downloads from npm and PyPI described above, plus queries to your own Astra DB database if you opt into the `astra` backend. Tool results go to Claude, and from there to Anthropic under your Claude terms, like any other tool output.

## License

MIT. See [LICENSE](LICENSE).
