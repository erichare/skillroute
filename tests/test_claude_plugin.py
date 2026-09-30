from __future__ import annotations

import json
import tomllib
from pathlib import Path

from skillroute.spec import validate_root

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "claude-plugin"


def _manifest() -> dict:
    return json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())


def _project_version() -> str:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_claude_plugin_version_tracks_the_release() -> None:
    assert _manifest()["version"] == _project_version()


def test_claude_plugin_pins_the_released_mcp_server() -> None:
    server = _manifest()["mcpServers"]["skillroute"]
    mcp_version = json.loads((REPO_ROOT / "mcp" / "package.json").read_text())["version"]

    # The plugin directory refuses unpinned launchers, and an exact pin keeps
    # the plugin on the server released alongside it.
    assert server["command"] == "npx"
    assert server["args"] == ["-y", f"@skillroute/mcp-server@{mcp_version}"]


def test_claude_plugin_ships_a_readme_and_license() -> None:
    assert (PLUGIN_ROOT / "LICENSE").read_text() == (REPO_ROOT / "LICENSE").read_text()
    readme_words = (PLUGIN_ROOT / "README.md").read_text().split()
    assert len(readme_words) >= 40
    assert _manifest()["license"] == "MIT"


def test_claude_plugin_skills_meet_the_agent_skills_spec() -> None:
    reports = validate_root(PLUGIN_ROOT / "skills")
    assert [report for report in reports if report.errors or report.warnings] == []
    assert len(reports) == 1


def test_codex_plugin_uses_the_same_skills_and_pinned_server() -> None:
    codex = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())
    assert codex["version"] == _project_version()
    assert codex["mcpServers"] == _manifest()["mcpServers"]
    assert (PLUGIN_ROOT / codex["skills"]).resolve() == (PLUGIN_ROOT / "skills").resolve()
    assert (PLUGIN_ROOT / codex["interface"]["logo"]).is_file()


def test_marketplaces_resolve_to_the_shared_plugin() -> None:
    claude = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    codex = json.loads((REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text())
    assert claude["name"] == codex["name"] == "skillroute-marketplace"
    assert claude["plugins"][0]["version"] == _project_version()
    assert (REPO_ROOT / claude["plugins"][0]["source"]).resolve() == PLUGIN_ROOT
    assert (REPO_ROOT / codex["plugins"][0]["source"]["path"]).resolve() == PLUGIN_ROOT
