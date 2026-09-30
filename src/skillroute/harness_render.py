"""Render a harness manifest into a concrete setup payload.

This replaces the if/elif chain that used to live in ``mcp_setup``. Every
per-client quirk that chain encoded is now either a manifest field or one of a
small, closed set of named *emitters* below.

An emitter owns exactly one config *shape*. Adding a harness whose config looks
like an existing shape is pure data; a genuinely new shape adds one function
here plus its test, which is a deliberate reviewable event rather than routine
work. Six of the fifteen shipped harnesses share ``mcp_servers`` unchanged.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from skillroute.catalog import default_catalog_path, user_catalog_path
from skillroute.harnesses import (
    HarnessManifest,
    InstallMode,
    current_platform,
    harness_ids,
    manifest_for,
)

PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

# Expand to several argv entries rather than one string, so they are only valid
# as a whole array element.
SPLAT_PLACEHOLDERS = frozenset({"server_argv"})

NPM_PACKAGE = "@skillroute/mcp-server"

# How a generated config names the MCP server. `local` points at a built
# checkout, which is the only thing that works before the package is published;
# `npx` points at the published package, which is the only thing that works for
# anyone who did not clone the repo.
SERVER_SOURCES = ("auto", "local", "npx")

# A source checkout can run its local server; a wheel has no mcp/build tree.
# Resolve this at setup time so package installs work without cloning the repo.
DEFAULT_SERVER_SOURCE = "auto"


class RenderError(ValueError):
    """A manifest referenced something the render context cannot supply."""


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def local_entrypoint(repo_root: Path) -> Path:
    return repo_root / "mcp" / "build" / "index.js"


def server_argv_for(
    source: str, *, repo_root: Path, package: str = NPM_PACKAGE
) -> list[str]:
    """The argv a generated config should use to start the MCP server."""
    if source == "auto":
        source = resolve_server_source(source, repo_root)
    if source == "npx":
        # `-y` so a first run does not stop at an install prompt inside a
        # harness that gives the server no terminal.
        return ["npx", "-y", package]
    if source == "local":
        return ["node", str(local_entrypoint(repo_root))]
    raise RenderError(
        f"Unknown server source {source!r}; expected one of: {', '.join(SERVER_SOURCES)}"
    )


def resolve_server_source(source: str, repo_root: Path) -> str:
    if source == "auto":
        return "local" if (repo_root / "src" / "skillroute").is_dir() else "npx"
    return source


def shell_command(parts: list[str]) -> str:
    return shlex.join(parts)


def build_harness_setup(
    *,
    harness: str,
    mode: str = "mcp",
    repo_root: Path | None = None,
    catalog: Path | None = None,
    backend: str | None = None,
    server_name: str = "skillroute",
    scope: str | None = None,
    platform: str | None = None,
    server_argv: list[str] | None = None,
    server_source: str | None = None,
) -> dict[str, Any]:
    """Build the install payload for one harness and mode."""
    manifest = manifest_for(harness)
    install = manifest.mode(mode)
    if install is None:
        available = ", ".join(sorted(manifest.modes)) or "none"
        raise ValueError(
            f"Harness {harness!r} does not support mode {mode!r}; available: {available}"
        )

    resolved_scope = _resolve_scope(install, scope, harness=harness)
    resolved_platform = platform or current_platform()
    resolved_repo_root = (repo_root or default_repo_root()).expanduser().resolve()
    resolved_source = resolve_server_source(
        server_source or DEFAULT_SERVER_SOURCE, resolved_repo_root
    )
    # A published server has no checkout, so resolving its catalog against one
    # would bake in a path that does not exist on the machine running the
    # config. Only a `local` config may name the checkout's catalog.
    catalog_path = (
        catalog.expanduser().resolve()
        if catalog
        else (
            default_catalog_path(resolved_repo_root)
            if resolved_source == "local"
            else user_catalog_path()
        )
    )
    selected_backend = (
        backend or os.environ.get("SKILLROUTE_BACKEND") or "local"
    ).strip().lower()
    entrypoint = local_entrypoint(resolved_repo_root)
    argv = (
        list(server_argv)
        if server_argv
        else server_argv_for(resolved_source, repo_root=resolved_repo_root)
    )

    env = {
        "SKILLROUTE_CATALOG_PATH": str(catalog_path),
        "SKILLROUTE_BACKEND": selected_backend,
    }
    stdio_server_config: dict[str, Any] = {
        "command": argv[0],
        "args": argv[1:],
        "env": env,
    }

    context: dict[str, Any] = {
        "harness_id": manifest.id,
        "server_name": server_name,
        "catalog": str(catalog_path),
        "backend": selected_backend,
        "repo_root": str(resolved_repo_root),
        # Where the server process should run. Only a checkout has a meaningful
        # working directory; an npx-resolved package does not, so this renders
        # empty and the key it fills is dropped below.
        "server_cwd": str(resolved_repo_root) if resolved_source == "local" else "",
        "scope": resolved_scope or "",
        "server_argv": argv,
    }

    # An extra that renders empty is one the manifest declared for a situation
    # that does not apply here (cwd under npx). Emitting `cwd: ""` would be
    # worse than omitting it -- some harnesses treat it as a real path.
    extra = {
        key: rendered
        for key, value in install.extra.items()
        if (rendered := _substitute(value, context)) != ""
    }
    emitter = EMITTERS.get(install.emitter)
    config: Any = None
    server_config: dict[str, Any] = stdio_server_config
    if emitter is not None:
        config, server_config = emitter(
            server_name=server_name,
            stdio=stdio_server_config,
            extra=extra,
            context=context,
        )

    # `{server_json}` needs the emitter's server_config, so it joins the context
    # only after the emitter has run.
    context["server_json"] = json.dumps(server_config, sort_keys=True)

    if resolved_source == "local":
        notes = [
            "Run ./scripts/bootstrap.sh first if mcp/build/index.js does not exist.",
            "The generated config points at this local checkout and catalog.",
        ]
        if not entrypoint.exists():
            notes.append(f"MCP entrypoint not found yet: {entrypoint}")
    else:
        notes = [
            f"The generated config runs the published {NPM_PACKAGE} via npx.",
            "npx resolves the package on first run, so that run is slower.",
        ]
    notes.extend(install.notes)

    install_command_parts = (
        _render_argv(install.argv, context) if install.setup_method == "command" else None
    )

    payload: dict[str, Any] = {
        "harness": manifest.id,
        # Retained so pre-0.2 callers of build_mcp_setup() keep working.
        "client": manifest.id,
        "mode": mode,
        "server_name": server_name,
        "repo_root": str(resolved_repo_root),
        "mcp_entrypoint": str(entrypoint),
        "server_source": resolved_source,
        "catalog": str(catalog_path),
        "backend": selected_backend,
        "server_config": server_config,
        "notes": notes,
        "install_command": (
            shell_command(install_command_parts) if install_command_parts else None
        ),
        "install_command_parts": install_command_parts,
        "config_path": _config_path_display(install, resolved_scope),
        "config_format": install.config_format,
        "setup_method": install.setup_method,
        "config": config,
        "write_path": install.path_for(resolved_platform, resolved_scope or ""),
    }
    if resolved_scope:
        payload["scope"] = resolved_scope
    return payload


def _resolve_scope(install: InstallMode, scope: str | None, *, harness: str) -> str | None:
    if not install.scopes:
        return None
    resolved = scope or install.default_scope
    if resolved and resolved not in install.scopes:
        valid = ", ".join(install.scopes)
        raise ValueError(
            f"Unsupported scope {resolved!r} for {harness}; expected one of: {valid}"
        )
    return resolved


def _config_path_display(install: InstallMode, scope: str | None) -> str:
    if scope and scope in install.scope_paths:
        return install.scope_paths[scope]
    return install.config_path_display


def _substitute(value: Any, context: dict[str, Any]) -> Any:
    """Replace ``{name}`` placeholders, raising on anything undeclared.

    Unknown placeholders are a manifest bug, and the conformance suite catches
    them at test time rather than letting them reach a user's terminal.
    """
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in context:
                raise RenderError(f"Unknown placeholder {{{key}}}")
            replacement = context[key]
            if isinstance(replacement, list):
                raise RenderError(
                    f"Placeholder {{{key}}} expands to multiple values and cannot be "
                    "used inside a string"
                )
            return str(replacement)

        return PLACEHOLDER.sub(replace, value)
    if isinstance(value, dict):
        return {key: _substitute(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, context) for item in value]
    return value


def _render_argv(argv: tuple[str, ...], context: dict[str, Any]) -> list[str]:
    rendered: list[str] = []
    for entry in argv:
        match = PLACEHOLDER.fullmatch(entry)
        if match and match.group(1) in SPLAT_PLACEHOLDERS:
            rendered.extend(str(item) for item in context[match.group(1)])
            continue
        rendered.append(_substitute(entry, context))
    return rendered


# --- emitters ------------------------------------------------------------
#
# Each returns (config, server_config). `config` is what gets written or printed;
# `server_config` is the single-server object, which some harnesses embed
# differently from how they store it.

EmitterResult = tuple[Any, dict[str, Any]]


def emit_mcp_servers(
    *, server_name: str, stdio: dict[str, Any], extra: dict[str, Any], context: dict[str, Any]
) -> EmitterResult:
    """`{"mcpServers": {name: {...}}}` -- the de facto standard shape."""
    return {"mcpServers": {server_name: {**stdio, **extra}}}, stdio


def emit_vscode_servers(
    *, server_name: str, stdio: dict[str, Any], extra: dict[str, Any], context: dict[str, Any]
) -> EmitterResult:
    """VS Code: top-level `servers`, and the name lives inside the object."""
    server_config = {"name": server_name, **stdio, **extra}
    return {"servers": {server_name: stdio}}, server_config


def emit_opencode_mcp(
    *, server_name: str, stdio: dict[str, Any], extra: dict[str, Any], context: dict[str, Any]
) -> EmitterResult:
    """OpenCode: nested under `mcp`, with an explicit local/remote type."""
    server_config = {
        "type": "local",
        "command": [stdio["command"], *stdio["args"]],
        "enabled": True,
        "environment": stdio["env"],
        **extra,
    }
    return {"mcp": {server_name: server_config}}, server_config


def emit_zed_context_servers(
    *, server_name: str, stdio: dict[str, Any], extra: dict[str, Any], context: dict[str, Any]
) -> EmitterResult:
    """Zed: `context_servers`, with the command split into its own object."""
    server_config = {
        "source": "custom",
        "command": stdio["command"],
        "args": stdio["args"],
        "env": stdio["env"],
        **extra,
    }
    return {"context_servers": {server_name: server_config}}, server_config


def emit_amp_mcp_servers(
    *, server_name: str, stdio: dict[str, Any], extra: dict[str, Any], context: dict[str, Any]
) -> EmitterResult:
    """Amp: same object shape, namespaced under an `amp.` settings key."""
    return {"amp.mcpServers": {server_name: {**stdio, **extra}}}, stdio


def emit_codex_toml(
    *, server_name: str, stdio: dict[str, Any], extra: dict[str, Any], context: dict[str, Any]
) -> EmitterResult:
    """Codex: a TOML snippet, rendered as text.

    Hand-rendered rather than written with a TOML library because SkillRoute
    never merges into a user's TOML file -- this is only ever printed.
    """
    lines = [
        f"[mcp_servers.{_toml_table_name(server_name)}]",
        f"command = {_toml_string(stdio['command'])}",
        f"args = [{', '.join(_toml_string(arg) for arg in stdio['args'])}]",
    ]
    if "cwd" in extra:
        lines.append(f"cwd = {_toml_string(str(extra['cwd']))}")
    env_pairs = ", ".join(
        f"{key} = {_toml_string(value)}" for key, value in sorted(stdio["env"].items())
    )
    lines.append(f"env = {{ {env_pairs} }}")
    for key, value in extra.items():
        if key == "cwd":
            continue
        lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines), stdio


def emit_yaml_map(
    *, server_name: str, stdio: dict[str, Any], extra: dict[str, Any], context: dict[str, Any]
) -> EmitterResult:
    """A YAML map under a configurable key, for Goose and Hermes.

    Hand-rendered for the same reason as the TOML emitter, and deliberately
    minimal: it only ever emits a flat map of scalars and string lists, which is
    all these configs need. Adding PyYAML for two harnesses is not worth it.
    """
    container = str(extra.get("container", "mcp_servers"))
    indent = "  "
    lines = [f"{container}:", f"{indent}{server_name}:"]
    lines.append(f"{indent * 2}cmd: {_yaml_scalar(stdio['command'])}")
    if stdio["args"]:
        lines.append(f"{indent * 2}args:")
        lines.extend(f"{indent * 3}- {_yaml_scalar(arg)}" for arg in stdio["args"])
    lines.append(f"{indent * 2}enabled: true")
    lines.append(f"{indent * 2}type: stdio")
    if stdio["env"]:
        lines.append(f"{indent * 2}envs:")
        lines.extend(
            f"{indent * 3}{key}: {_yaml_scalar(value)}"
            for key, value in sorted(stdio["env"].items())
        )
    return "\n".join(lines), stdio


def emit_dsh_cordis_patch(
    *, server_name: str, stdio: dict[str, Any], extra: dict[str, Any], context: dict[str, Any]
) -> EmitterResult:
    """DeepSeek Harness: a cordis.patch.yml insert for its MCP client plugin.

    dsh loads every capability as a Cordis plugin, and MCP servers arrive
    through @deepseek-ai/dsh-mcp-client. A server is added by inserting one
    entry into a profile's cordis.patch.yml -- a YAML list of patch options,
    which SkillRoute therefore prints rather than merges.
    """
    indent = "  "
    lines = [
        "# SkillRoute MCP server for DeepSeek Harness.",
        "# Append to ~/.dsh/cordis.patch.yml (all profiles) or",
        "# ~/.dsh/profiles/<name>/cordis.patch.yml (one profile).",
        "- insert:",
        f"{indent}- id: mcp-skillroute",
        f"{indent}  name: '@deepseek-ai/dsh-mcp-client'",
        f"{indent}  config:",
        f"{indent}    serverName: {_yaml_scalar(server_name)}",
        f"{indent}    transport: stdio",
        f"{indent}    command: {_yaml_scalar(stdio['command'])}",
    ]
    if stdio["args"]:
        lines.append(f"{indent}    args:")
        lines.extend(f"{indent}      - {_yaml_scalar(arg)}" for arg in stdio["args"])
    if stdio["env"]:
        lines.append(f"{indent}    env:")
        lines.extend(
            f"{indent}      {key}: {_yaml_scalar(value)}"
            for key, value in sorted(stdio["env"].items())
        )
    return "\n".join(lines), stdio


def emit_claude_session_start_hook(
    *, server_name: str, stdio: dict[str, Any], extra: dict[str, Any], context: dict[str, Any]
) -> EmitterResult:
    """A Claude Code SessionStart hook entry."""
    command = shell_command(["skillroute", "hook", "session-start"])
    config = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": command, "timeout": 20}],
                }
            ]
        }
    }
    return config, stdio


Emitter = Callable[..., EmitterResult]

EMITTERS: dict[str, Emitter] = {
    "mcp_servers": emit_mcp_servers,
    "vscode_servers": emit_vscode_servers,
    "opencode_mcp": emit_opencode_mcp,
    "zed_context_servers": emit_zed_context_servers,
    "amp_mcp_servers": emit_amp_mcp_servers,
    "codex_toml": emit_codex_toml,
    "yaml_map": emit_yaml_map,
    "dsh_cordis_patch": emit_dsh_cordis_patch,
    "claude_session_start_hook": emit_claude_session_start_hook,
}


def _toml_table_name(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return _toml_string(value)


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return _toml_string(str(value))


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text and re.fullmatch(r"[A-Za-z0-9_./@:-]+", text):
        return text
    return json.dumps(text)


def mode_label(mode: str) -> str:
    """Display form of a mode name: acronyms stay uppercase, the rest read as words."""
    acronyms = {"mcp": "MCP", "acp": "ACP"}
    return acronyms.get(mode, mode.replace("_", " "))


def render_harness_setup(payload: dict[str, Any]) -> str:
    manifest = manifest_for(payload["harness"])
    lines = [
        f"SkillRoute {mode_label(payload['mode'])} setup for {manifest.display_name}",
        f"server: {payload['server_name']}",
        f"catalog: {payload['catalog']}",
        f"backend: {payload['backend']}",
        "",
    ]
    if payload.get("install_command"):
        lines.extend(["Install command:", payload["install_command"], ""])
    lines.extend(
        [f"Config path: {payload['config_path']}", f"Config format: {payload['config_format']}", ""]
    )
    lines.append("Config snippet:")
    config = payload["config"]
    if isinstance(config, str):
        lines.append(config)
    else:
        lines.append(json.dumps(config, indent=2, sort_keys=True))
    if payload.get("notes"):
        lines.extend(["", "Notes:"])
        lines.extend(f"- {note}" for note in payload["notes"])
    return "\n".join(lines)


def harness_display_name(harness: str) -> str:
    try:
        return manifest_for(harness).display_name
    except ValueError:
        return harness


def supported_harnesses() -> tuple[str, ...]:
    return harness_ids()


def modes_for(harness: str) -> tuple[str, ...]:
    return tuple(sorted(manifest_for(harness).modes))


def manifests_supporting(mode: str) -> list[HarnessManifest]:
    from skillroute.harnesses import load_manifests

    return [
        manifest for manifest in load_manifests().values() if manifest.supports(mode)
    ]
