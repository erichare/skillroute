# Agent Setup

SkillRoute exposes a local stdio MCP server, so agent clients can route skill
requests without a hosted service.

## Install the Claude Code or Codex plugin

With Node.js 20+ and uv installed:

```bash
# Claude Code
claude plugin marketplace add jestatsio/skillroute
claude plugin install skillroute@skillroute-marketplace

# Codex
codex plugin marketplace add jestatsio/skillroute
codex plugin add skillroute@skillroute-marketplace

# Index existing skill roots, then restart your agent
uvx skillroute dogfood index
```

The plugins include a pinned MCP server and shared routing guidance. No source build or JEStats
account is needed. See the [plugin README](../claude-plugin/README.md) for prerequisites and privacy.

## Install from packages for any agent

```bash
uv tool install skillroute
skillroute dogfood index
skillroute harness install codex     # or claude-code, cursor, vscode, ...
skillroute harness install codex --dry-run   # preview the configuration
```

Package installs automatically use `npx -y @skillroute/mcp-server`; source checkouts use their
local build. Use `--server-source npx` or `--server-source local` to choose explicitly. JSON
edits preserve existing servers and create a backup; TOML/YAML setups use the agent's CLI or
print a snippet. `skillroute harness list` shows all 15 supported clients and their install modes.

## Source installer

For a fresh SkillRoute install:

```bash
curl -fsSL https://raw.githubusercontent.com/jestatsio/skillroute/main/scripts/install.sh | bash
```

The installer confirms each step, installs SkillRoute into
`~/.skillroute/skillroute` by default (an existing `skill-route` checkout is reused), builds the MCP server, indexes starter
skills, detects supported agent clients, and offers setup for each detected
client. JSON config edits preserve unrelated servers and create timestamped
backups.

For unattended use:

```bash
curl -fsSL https://raw.githubusercontent.com/jestatsio/skillroute/main/scripts/install.sh | bash -s -- --yes
```

Useful installer options:

```bash
curl -fsSL https://raw.githubusercontent.com/jestatsio/skillroute/main/scripts/install.sh \
  | SKILLROUTE_INSTALL_DIR=/opt/skillroute bash

curl -fsSL https://raw.githubusercontent.com/jestatsio/skillroute/main/scripts/install.sh \
  | bash -s -- --clients codex,claude-code,vscode --yes

curl -fsSL https://raw.githubusercontent.com/jestatsio/skillroute/main/scripts/install.sh \
  | SKILLROUTE_CLIENT_SETUP=0 bash
```

Use `--no-client-setup` when you want detection output without config changes.
Use `--clients auto`, `--clients all`, or a comma-separated list such as
`--clients ibm-bob,codex,windsurf`.

Already in a checkout:

```bash
./scripts/bootstrap.sh
```

The installer and bootstrap script:

- installs the Python dev environment
- installs Node dependencies for the MCP server
- builds `mcp/build/index.js`
- indexes the example skills into `.skillroute/catalog.db`
- prints setup commands for supported agent clients

The installer can configure IBM Bob, Codex, Claude Code, Claude Desktop, VS Code,
and Windsurf. Cursor is detected and shown as a snippet only until a stable
official write target is confirmed.

## IBM Bob

IBM Bob is the primary first integration for SkillRoute. Bob already supports MCP,
and SkillRoute's local stdio server gives Bob three tools:

- `skillroute.route`
- `skillroute.search`
- `skillroute.inspect_skill`

Generate a Bob-ready `mcpServers` block:

```bash
uv run skillroute mcp config --client ibm-bob
```

Paste the generated JSON into one of Bob's MCP config files:

```text
Global:  ~/.bob/mcp.json
Project: .bob/mcp.json
```

Use global config for your own machine. Use project config only when the paths
are team-safe, for example through environment variables or a shared install
location.

In Bob, open the MCP settings panel and make sure MCP servers are enabled.
SkillRoute does not add `alwaysAllow`; Bob should ask before using tools unless
you explicitly change tool approval settings.

See the official [IBM Bob MCP docs](https://bob.ibm.com/docs/ide/configuration/mcp/mcp-in-bob)
and [transport guide](https://bob.ibm.com/docs/ide/configuration/mcp/server-transports).

## Codex

Generate a reviewed setup command and TOML snippet:

```bash
uv run skillroute mcp config --client codex
```

Run the printed `codex mcp add ...` command, or paste the TOML into:

```text
~/.codex/config.toml
```

Codex stores MCP servers in `config.toml`, and the CLI and IDE extension share
that config. See the official [Codex MCP docs](https://developers.openai.com/codex/mcp).

## Claude Code

Generate a Claude Code setup command:

```bash
uv run skillroute mcp config --client claude-code
```

The default scope is `user`, which makes SkillRoute available across your
projects while keeping the config private to your machine.

For a team-shared project config:

```bash
uv run skillroute mcp config --client claude-code --scope project
```

That emits a `.mcp.json`-compatible snippet. The generated snippet uses absolute
local paths, so replace those with team-safe environment variables before
checking it in. See the official [Claude Code MCP docs](https://code.claude.com/docs/en/mcp).

## Claude Desktop

Generate the JSON block:

```bash
uv run skillroute mcp config --client claude-desktop
```

Paste the generated `mcpServers` block into Claude Desktop's config file:

```text
macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json
Windows: %APPDATA%\Claude\claude_desktop_config.json
```

Restart Claude Desktop after editing the file. See the official
[local MCP server guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

## VS Code

Generate a reviewed `code --add-mcp` command and profile snippet:

```bash
uv run skillroute mcp config --client vscode
```

The installer uses the VS Code CLI when `code` or `code-insiders` is available.
Manual config uses a top-level `servers` object. See the official
[VS Code MCP docs](https://code.visualstudio.com/docs/agent-customization/mcp-servers).

## Windsurf

Generate a Windsurf-ready `mcpServers` block:

```bash
uv run skillroute mcp config --client windsurf
```

The installer writes `~/.codeium/windsurf/mcp_config.json` when Windsurf is
selected, preserving unrelated servers and creating a backup if the file already
exists. See the official
[Windsurf MCP docs](https://docs.devin.ai/desktop/cascade/mcp).

## Cursor

Generate a Cursor-compatible snippet:

```bash
uv run skillroute mcp config --client cursor
```

Cursor is detect-and-print only in V1. SkillRoute does not write Cursor config
until the official config path and schema are stable enough to automate safely.

## Codex Plugin Status

V1 uses direct MCP setup because the server currently points at a local source
checkout. That is the least surprising path for early users.

The next packaging step is a Codex plugin that bundles:

- `.codex-plugin/plugin.json`
- `.mcp.json`
- friendly assets and screenshots
- a package entrypoint that does not require editing paths by hand

Codex plugins can bundle MCP servers through an `.mcp.json` file, so the setup
generator is intentionally close to that future shape. See the official
[Codex plugin docs](https://developers.openai.com/codex/plugins/build).

## Useful Options

```bash
uv run skillroute mcp config --client ibm-bob --backend astra
uv run skillroute mcp config --client codex --backend astra
uv run skillroute mcp config --client claude-code --catalog /path/to/catalog.db
uv run skillroute mcp config --client claude-desktop --server-name skillroute-dev
uv run skillroute mcp config --client vscode --server-name skillroute-dev
uv run skillroute mcp config --client windsurf --catalog /path/to/catalog.db
```

Generated config includes only local paths and SkillRoute backend selection. Keep
remote backend credentials in your shell or client-specific private config, not
in checked-in project files.

## Troubleshooting

```bash
./scripts/bootstrap.sh
npm --prefix mcp run smoke
uv run skillroute backend status --backend local
uv run skillroute route "Build an MCP server"
```

If a client cannot start the server, regenerate config and check that
`mcp/build/index.js` exists at the absolute path shown in the output.
