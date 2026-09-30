"""Conformance tests over every harness manifest.

These are parametrized across whatever is in ``harnesses/``, so adding a harness
adds its coverage automatically: drop in a ``.toml`` and these tests start
checking it. That is the property that makes a new harness cheap to contribute.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillroute.harness_render import (
    EMITTERS,
    PLACEHOLDER,
    RenderError,
    build_harness_setup,
    render_harness_setup,
)
from skillroute.harness_setup import HarnessEnvironment, detect_harness
from skillroute.harnesses import (
    INSTALL_MODES,
    PLATFORMS,
    SETUP_METHODS,
    ManifestError,
    current_platform,
    harness_for_client_name,
    load_manifests,
    manifest_for,
    parse_manifest,
    skill_discovery_globs,
)

MANIFESTS = load_manifests()
HARNESS_IDS = sorted(MANIFESTS)
# Every (harness, mode) pair in the repo, as test ids like "pi/skills".
MODE_PAIRS = [
    pytest.param(harness, mode, id=f"{harness}/{mode}")
    for harness in HARNESS_IDS
    for mode in sorted(MANIFESTS[harness].modes)
]
REPO_ROOT = Path("/repo")
CATALOG = Path("/repo/.skillroute/catalog.db")

# The clients v0.1 shipped. They must keep working through the deprecated shim.
LEGACY_CLIENTS = {
    "ibm-bob",
    "codex",
    "claude-code",
    "claude-desktop",
    "vscode",
    "windsurf",
    "cursor",
}


def test_manifests_are_present() -> None:
    assert MANIFESTS, "no harness manifests found -- is harnesses/ packaged?"
    assert LEGACY_CLIENTS <= set(MANIFESTS)


@pytest.mark.parametrize("harness", HARNESS_IDS)
def test_manifest_is_well_formed(harness: str) -> None:
    manifest = MANIFESTS[harness]
    assert manifest.id == harness
    assert manifest.display_name.strip()
    assert manifest.modes, f"{harness} declares no install modes"
    assert set(manifest.modes) <= set(INSTALL_MODES)
    # Every harness must at least be reachable over MCP.
    assert manifest.supports("mcp"), f"{harness} does not support mcp"


@pytest.mark.parametrize("harness", HARNESS_IDS)
def test_declared_emitters_and_methods_exist(harness: str) -> None:
    for mode in MANIFESTS[harness].modes.values():
        assert mode.setup_method in SETUP_METHODS
        if mode.emitter:
            assert mode.emitter in EMITTERS, (
                f"{harness}/{mode.mode} names unknown emitter {mode.emitter!r}"
            )


@pytest.mark.parametrize(("harness", "mode"), MODE_PAIRS)
def test_every_mode_renders_on_every_platform(harness: str, mode: str) -> None:
    """No manifest may reference a placeholder the renderer cannot supply."""
    for platform in PLATFORMS:
        payload = build_harness_setup(
            harness=harness,
            mode=mode,
            repo_root=REPO_ROOT,
            catalog=CATALOG,
            backend="local",
            platform=platform,
        )
        assert payload["harness"] == harness
        assert payload["mode"] == mode
        assert payload["setup_method"] in SETUP_METHODS
        # A command install is useless without argv to run.
        if payload["setup_method"] == "command":
            assert payload["install_command_parts"]
            assert payload["install_command"]


@pytest.mark.parametrize(("harness", "mode"), MODE_PAIRS)
def test_every_mode_renders_human_output(harness: str, mode: str) -> None:
    payload = build_harness_setup(
        harness=harness, mode=mode, repo_root=REPO_ROOT, catalog=CATALOG
    )
    text = render_harness_setup(payload)
    assert MANIFESTS[harness].display_name in text
    # A leftover {placeholder} means the manifest named something the renderer
    # never filled in. Braces themselves are fine -- VS Code's install command
    # legitimately embeds a JSON object.
    assert not PLACEHOLDER.search(text), (
        f"unsubstituted placeholder in rendered output: {PLACEHOLDER.search(text)}"
    )


@pytest.mark.parametrize("harness", HARNESS_IDS)
def test_no_manifest_merges_a_format_we_cannot_write(harness: str) -> None:
    """SkillRoute never rewrites a user's TOML or YAML.

    ``tomllib`` cannot write, we carry no YAML dependency, and hand-merging
    someone's config file is not worth the blast radius. Those formats must
    install via a command or be printed for the user to paste.
    """
    for mode in MANIFESTS[harness].modes.values():
        if mode.config_format in {"toml", "yaml"}:
            assert mode.setup_method != "json_merge", (
                f"{harness}/{mode.mode} would merge {mode.config_format}"
            )


@pytest.mark.parametrize("harness", HARNESS_IDS)
def test_detection_finds_a_harness_from_its_own_manifest(
    harness: str, tmp_path: Path
) -> None:
    """Synthesize an environment from what the manifest declares, and find it."""
    manifest = MANIFESTS[harness]
    home = tmp_path / "home"
    for platform in PLATFORMS:
        paths = manifest.detect.paths_for(platform) + manifest.detect.apps_for(platform)
        commands = manifest.detect.commands
        if not paths and not commands:
            continue
        env = HarnessEnvironment(home=home, commands={}, existing_paths=set(), platform=platform)
        populated = HarnessEnvironment(
            home=home,
            commands={command: f"/usr/bin/{command}" for command in commands},
            existing_paths={env.resolve(raw) for raw in paths},
            platform=platform,
        )
        assert detect_harness(manifest, populated).detected is True
        assert detect_harness(manifest, env).detected is False


@pytest.mark.parametrize("harness", HARNESS_IDS)
def test_client_names_map_back_to_their_harness(harness: str) -> None:
    for client_name in MANIFESTS[harness].detect.client_names:
        if "*" in client_name:
            continue
        assert harness_for_client_name(client_name) is not None


def test_client_name_matching_is_case_insensitive_and_globbed() -> None:
    assert harness_for_client_name("Claude-Code") == "claude-code"
    assert harness_for_client_name("claude-code-web") == "claude-code"
    assert harness_for_client_name("") is None
    assert harness_for_client_name("no-such-editor") is None


def test_skill_discovery_covers_the_harnesses_that_have_skills() -> None:
    globs = skill_discovery_globs()
    # The v0.1 hardcoded list missed Claude Code entirely.
    assert any(".claude/skills" in glob for glob in globs)
    assert any(".codex/skills" in glob for glob in globs)
    assert any("pi/agent/skills" in glob for glob in globs)


def test_unknown_harness_is_rejected_with_the_valid_list() -> None:
    with pytest.raises(ValueError, match="Unsupported harness"):
        manifest_for("not-a-harness")


def test_unsupported_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not support mode"):
        build_harness_setup(harness="amp", mode="extension")


def test_unsupported_scope_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported scope"):
        build_harness_setup(harness="claude-code", mode="mcp", scope="galaxy")


# --- manifest validation -------------------------------------------------


def minimal_manifest(**overrides: object) -> dict:
    base = {
        "schema": 1,
        "id": "demo",
        "display_name": "Demo",
        "modes": {"mcp": {"setup_method": "json_merge", "emitter": "mcp_servers"}},
    }
    base.update(overrides)
    return base


def test_parse_manifest_accepts_a_minimal_manifest() -> None:
    manifest = parse_manifest(minimal_manifest())
    assert manifest.id == "demo"
    assert manifest.tier == "breadth"


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"schema": 99}, "schema must be"),
        ({"id": ""}, "missing required string field"),
        ({"tier": "gold"}, "tier"),
        ({"modes": {"telepathy": {"setup_method": "json_merge"}}}, "unknown install mode"),
        ({"modes": {"mcp": {"setup_method": "carrier_pigeon"}}}, "setup_method"),
        ({"modes": {"mcp": {"setup_method": "command"}}}, "requires argv"),
        ({"detect": {"paths": {"solaris": ["~/x"]}}}, "must be 'all' or one of"),
        ({"detect": {"commands": "claude"}}, "must be a list of strings"),
        (
            {
                "modes": {
                    "mcp": {
                        "setup_method": "json_merge",
                        "scopes": ["user"],
                        "default_scope": "project",
                    }
                }
            },
            "default_scope",
        ),
    ],
)
def test_parse_manifest_rejects_malformed_input(overrides: dict, match: str) -> None:
    with pytest.raises(ManifestError, match=match):
        parse_manifest(minimal_manifest(**overrides))


def test_render_rejects_an_unknown_placeholder() -> None:
    from skillroute.harness_render import _substitute

    with pytest.raises(RenderError, match="Unknown placeholder"):
        _substitute("{nonsense}", {"catalog": "/x"})


def test_splat_placeholder_cannot_be_used_inside_a_string() -> None:
    from skillroute.harness_render import _substitute

    with pytest.raises(RenderError, match="expands to multiple values"):
        _substitute("prefix-{server_argv}", {"server_argv": ["node", "index.js"]})


# --- emitter shapes ------------------------------------------------------
#
# One assertion per emitter, pinning the shape that harness actually expects.


def setup(harness: str, mode: str = "mcp", **kwargs: object) -> dict:
    return build_harness_setup(
        harness=harness, mode=mode, repo_root=REPO_ROOT, catalog=CATALOG, backend="local",
        server_source="local", **kwargs
    )


def test_mcp_servers_shape() -> None:
    config = setup("claude-code")["config"]
    assert list(config) == ["mcpServers"]
    assert config["mcpServers"]["skillroute"]["command"] == "node"


def test_vscode_uses_servers_key_and_embeds_the_name() -> None:
    payload = setup("vscode")
    assert list(payload["config"]) == ["servers"]
    # VS Code's --add-mcp payload carries the name inside the object.
    assert payload["server_config"]["name"] == "skillroute"
    assert payload["install_command_parts"][1] == "--add-mcp"


def test_ibm_bob_adds_cwd_and_disabled() -> None:
    server = setup("ibm-bob")["config"]["mcpServers"]["skillroute"]
    assert server["cwd"] == str(REPO_ROOT)
    assert server["disabled"] is False


def test_codex_emits_toml_with_timeouts() -> None:
    payload = setup("codex")
    assert payload["config_format"] == "toml"
    assert "[mcp_servers.skillroute]" in payload["config"]
    assert "startup_timeout_sec = 20" in payload["config"]
    assert "tool_timeout_sec = 60" in payload["config"]


def test_opencode_nests_under_mcp_with_a_type() -> None:
    server = setup("opencode")["config"]["mcp"]["skillroute"]
    assert server["type"] == "local"
    assert server["enabled"] is True
    assert server["command"][0] == "node"


def test_zed_uses_context_servers() -> None:
    assert list(setup("zed")["config"]) == ["context_servers"]


def test_amp_namespaces_its_settings_key() -> None:
    assert list(setup("amp")["config"]) == ["amp.mcpServers"]


def test_hermes_and_goose_emit_yaml_under_their_own_container() -> None:
    hermes = setup("hermes")
    assert hermes["config_format"] == "yaml"
    assert hermes["config"].startswith("mcp_servers:")
    assert hermes["setup_method"] == "print_only"

    goose = setup("goose")
    assert goose["config"].startswith("extensions:")


def test_deepseek_emits_a_cordis_patch_insert() -> None:
    payload = setup("deepseek")
    assert payload["config_format"] == "yaml"
    assert payload["setup_method"] == "print_only"
    config = payload["config"]
    assert "- insert:" in config
    assert "name: '@deepseek-ai/dsh-mcp-client'" in config
    assert "serverName: skillroute" in config
    assert "transport: stdio" in config
    assert "command: node" in config
    assert "SKILLROUTE_CATALOG_PATH:" in config


def test_gemini_cli_reuses_the_standard_shape_with_no_new_emitter() -> None:
    """Pure-data harness: proof the manifest system pays off."""
    assert MANIFESTS["gemini-cli"].mode("mcp").emitter == "mcp_servers"
    assert list(setup("gemini-cli")["config"]) == ["mcpServers"]


def test_claude_code_scope_changes_the_config_path() -> None:
    assert setup("claude-code", scope="user")["config_path"] == "~/.claude.json"
    assert setup("claude-code", scope="project")["config_path"] == ".mcp.json"
    assert "--scope" in setup("claude-code", scope="project")["install_command_parts"]


def test_claude_hook_emits_a_session_start_entry() -> None:
    config = setup("claude-code", mode="hook")["config"]
    assert "SessionStart" in config["hooks"]


def test_windows_paths_are_declared_where_they_differ() -> None:
    """Cross-platform coverage is the point of per-platform manifest paths."""
    goose = MANIFESTS["goose"].mode("mcp")
    assert goose.path_for("windows") != goose.path_for("linux")
    assert "APPDATA" in goose.path_for("windows")


def test_current_platform_is_one_we_know() -> None:
    assert current_platform() in PLATFORMS


# --- CLI surface ---------------------------------------------------------


def test_cli_harness_list_json_covers_every_manifest(capsys) -> None:
    from skillroute.cli import main

    main(["harness", "list", "--json"])
    payload = __import__("json").loads(capsys.readouterr().out)
    assert {entry["id"] for entry in payload} == set(HARNESS_IDS)
    assert all(entry["modes"] for entry in payload)


def test_cli_harness_list_filters_by_mode(capsys) -> None:
    from skillroute.cli import main

    main(["harness", "list", "--mode", "extension"])
    out = capsys.readouterr().out
    assert "pi" in out
    assert "amp" not in out


def test_cli_harness_show_renders_for_another_platform(capsys) -> None:
    from skillroute.cli import main

    main(["harness", "show", "goose", "--platform", "windows", "--json"])
    payload = __import__("json").loads(capsys.readouterr().out)
    assert "APPDATA" in payload["write_path"]


def test_cli_harness_install_dry_run_changes_nothing(tmp_path: Path, capsys) -> None:
    from skillroute.cli import main

    main(
        [
            "harness",
            "install",
            "claude-code",
            "--dry-run",
            "--repo-root",
            str(tmp_path),
        ]
    )
    assert "SkillRoute MCP setup for Claude Code" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_cli_install_applies_the_selected_source_and_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from skillroute import cli, harness_setup

    (tmp_path / "src" / "skillroute").mkdir(parents=True)
    env = HarnessEnvironment(
        home=tmp_path, commands={"claude": "/bin/claude"}, existing_paths=set()
    )
    monkeypatch.setattr(cli, "detect_harnesses", lambda: harness_setup.detect_harnesses(env))
    calls: list[list[str]] = []
    monkeypatch.setattr(harness_setup.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    cli.main([
        "harness", "install", "claude-code", "--repo-root", str(tmp_path),
        "--server-source", "npx", "--scope", "project", "--yes",
    ])
    assert len(calls) == 1
    assert calls[0][-3:] == ["npx", "-y", "@skillroute/mcp-server"]
    assert calls[0][calls[0].index("--scope") + 1] == "project"


def test_cli_mcp_config_deprecation_goes_to_stderr_only(capsys) -> None:
    """`--json` stdout must stay machine-parseable."""
    from skillroute.cli import main

    main(["mcp", "config", "--client", "codex", "--json"])
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "deprecated" not in captured.out
    assert __import__("json").loads(captured.out)["harness"] == "codex"


# --- server source (S2) ---------------------------------------------------


def test_server_argv_local_points_at_the_checkout(tmp_path: Path) -> None:
    from skillroute.harness_render import server_argv_for

    argv = server_argv_for("local", repo_root=tmp_path)
    assert argv[0] == "node"
    assert argv[1].endswith("mcp/build/index.js")


def test_server_argv_npx_points_at_the_published_package(tmp_path: Path) -> None:
    from skillroute.harness_render import NPM_PACKAGE, server_argv_for

    assert server_argv_for("npx", repo_root=tmp_path) == ["npx", "-y", NPM_PACKAGE]


def test_server_argv_rejects_an_unknown_source(tmp_path: Path) -> None:
    from skillroute.harness_render import RenderError, server_argv_for

    with pytest.raises(RenderError, match="Unknown server source"):
        server_argv_for("carrier-pigeon", repo_root=tmp_path)


def test_default_setup_works_without_a_checkout(tmp_path: Path) -> None:
    payload = build_harness_setup(harness="codex", repo_root=tmp_path)
    assert payload["server_config"]["command"] == "npx"
    assert payload["server_config"]["args"] == ["-y", "@skillroute/mcp-server"]
    assert "cwd" not in payload["server_config"]
    assert "MCP entrypoint not found" not in " ".join(payload["notes"])


def test_default_setup_preserves_source_checkout_workflow(tmp_path: Path) -> None:
    (tmp_path / "src" / "skillroute").mkdir(parents=True)
    payload = build_harness_setup(harness="codex", repo_root=tmp_path)
    assert payload["server_source"] == "local"
    assert payload["server_config"]["command"] == "node"


@pytest.mark.parametrize("harness", sorted(load_manifests()))
def test_every_harness_renders_against_both_server_sources(
    harness: str, tmp_path: Path
) -> None:
    from skillroute.harness_render import SERVER_SOURCES, build_harness_setup

    for source in SERVER_SOURCES:
        payload = build_harness_setup(
            harness=harness,
            mode="mcp",
            repo_root=tmp_path,
            catalog=tmp_path / "c.db",
            server_source=source,
        )
        assert payload["server_source"] == ("npx" if source == "auto" else source)


def test_npx_source_drops_the_local_checkout_notes(tmp_path: Path) -> None:
    from skillroute.harness_render import build_harness_setup

    payload = build_harness_setup(
        harness="claude-code",
        mode="mcp",
        repo_root=tmp_path,
        catalog=tmp_path / "c.db",
        server_source="npx",
    )
    joined = " ".join(payload["notes"])
    assert "bootstrap.sh" not in joined
    assert "npx" in joined


def declares_key(config: object, key: str) -> bool:
    """Is `key` a config *key*, not just a substring somewhere in a path?

    Configs come back as dicts for JSON harnesses and as rendered text for the
    TOML one, so both shapes need checking.
    """
    if isinstance(config, dict):
        return key in config or any(declares_key(v, key) for v in config.values())
    if isinstance(config, list):
        return any(declares_key(item, key) for item in config)
    if isinstance(config, str):
        return any(
            line.strip().startswith(f"{key} =") for line in config.splitlines()
        )
    return False


@pytest.mark.parametrize("harness", ["codex", "ibm-bob"])
def test_working_directory_is_dropped_for_a_published_server(
    harness: str, tmp_path: Path
) -> None:
    """An npx-resolved package has no checkout, so cwd must not be emitted."""
    from skillroute.harness_render import build_harness_setup

    local = build_harness_setup(
        harness=harness, mode="mcp", repo_root=tmp_path, catalog=tmp_path / "c.db",
        server_source="local",
    )
    npx = build_harness_setup(
        harness=harness,
        mode="mcp",
        repo_root=tmp_path,
        catalog=tmp_path / "c.db",
        server_source="npx",
    )
    assert declares_key(local["config"], "cwd")
    assert not declares_key(npx["config"], "cwd")


def test_no_generated_config_leaks_a_checkout_path_under_npx(tmp_path: Path) -> None:
    """The whole point of S2: an npx config must work off a clone."""
    from skillroute.harness_render import build_harness_setup

    marker = str(tmp_path)
    for harness in sorted(load_manifests()):
        payload = build_harness_setup(
            harness=harness,
            mode="mcp",
            repo_root=tmp_path,
            catalog=tmp_path / "c.db",
            server_source="npx",
        )
        rendered = json.dumps(payload["config"]) + json.dumps(payload["server_config"])
        # The catalog path is excluded deliberately, and it is the one thing
        # still checkout-bound under npx: default_catalog_path() resolves to
        # <repo_root>/.skillroute/catalog.db. Where a published install should
        # keep its catalog is an open decision, so this asserts the rest of the
        # config is clean rather than pretending that part is settled.
        leaked = [
            line for line in rendered.split('"') if marker in line and "c.db" not in line
        ]
        assert not leaked, f"{harness} leaked a checkout path: {leaked}"
