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
