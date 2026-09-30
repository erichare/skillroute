"""Detect installed harnesses and apply their setup.

v0.1 had one hand-written ``detect_*`` function per client plus a hardcoded
ordering tuple. Detection is now one loop over the manifests: a harness is
present if any command it declares is on PATH, or any path or app bundle it
declares exists.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from skillroute.catalog import default_catalog_path
from skillroute.harness_render import build_harness_setup
from skillroute.harnesses import (
    HarnessManifest,
    current_platform,
    harness_ids,
    load_manifests,
)

SETUP_CHOICES = ("prompt", "0", "1")
NO_TTY_SETUP_MESSAGE = (
    "No terminal is available for client setup prompts; skipping automatic client configuration. "
    "Re-run with --yes or SKILLROUTE_CLIENT_SETUP=1 to configure detected clients, "
    "or use SKILLROUTE_CLIENT_SETUP=0 to skip this step."
)


@dataclass(frozen=True, slots=True)
class HarnessDetection:
    id: str
    name: str
    detected: bool
    reason: str
    setup_method: str
    command: str | None = None
    config_path: str | None = None


@dataclass(frozen=True, slots=True)
class SetupResult:
    client: str
    status: str
    message: str
    backup_path: str | None = None


class HarnessEnvironment:
    """Injectable view of the machine, so detection is testable without one."""

    def __init__(
        self,
        *,
        home: Path | None = None,
        commands: dict[str, str | None] | None = None,
        existing_paths: set[Path] | None = None,
        platform: str | None = None,
    ) -> None:
        self.home = (home or Path.home()).expanduser()
        self.platform = platform or current_platform()
        self._commands = commands
        self._existing_paths = (
            {path.expanduser() for path in existing_paths}
            if existing_paths is not None
            else None
        )

    def which(self, command: str) -> str | None:
        if self._commands is not None:
            return self._commands.get(command)
        return shutil.which(command)

    def exists(self, path: Path) -> bool:
        expanded = path.expanduser()
        if self._existing_paths is not None:
            return expanded in self._existing_paths
        return expanded.exists()

    def resolve(self, raw: str) -> Path:
        """Expand a manifest path against this environment's home.

        Tests inject a fake home, so ``~`` must not go through
        ``Path.expanduser()``, which would read the real one.
        """
        expanded = os.path.expandvars(raw)
        if expanded.startswith("~"):
            return self.home / expanded.lstrip("~/\\")
        return Path(expanded)


def detect_harnesses(env: HarnessEnvironment | None = None) -> list[HarnessDetection]:
    env = env or HarnessEnvironment()
    detections = [
        detect_harness(manifest, env) for manifest in load_manifests().values()
    ]
    return sorted(detections, key=lambda detection: detection.id)


def detect_harness(manifest: HarnessManifest, env: HarnessEnvironment) -> HarnessDetection:
    mcp = manifest.mode("mcp")
    setup_method = mcp.setup_method if mcp else "print_only"
    config_path = mcp.path_for(env.platform) if mcp else ""

    found_command: str | None = None
    for command in manifest.detect.commands:
        resolved = env.which(command)
        if resolved:
            found_command = resolved
            break

    found_path: Path | None = None
    for raw in (*manifest.detect.paths_for(env.platform), *manifest.detect.apps_for(env.platform)):
        candidate = env.resolve(raw)
        if env.exists(candidate):
            found_path = candidate
            break

    detected = bool(found_command) or found_path is not None
    if found_command:
        reason = f"found {found_command}"
    elif found_path is not None:
        reason = f"found {found_path}"
    elif manifest.detect.commands and not (
        manifest.detect.paths_for(env.platform) or manifest.detect.apps_for(env.platform)
    ):
        # Command-only harnesses name the binary they looked for; anything with
        # paths to check would make that message misleading.
        reason = f"{'/'.join(manifest.detect.commands)} command not found"
    else:
        reason = "not found"

    return HarnessDetection(
        id=manifest.id,
        name=manifest.display_name,
        detected=detected,
        reason=reason,
        setup_method=setup_method,
        command=found_command,
        config_path=str(env.resolve(config_path)) if config_path else None,
    )


def select_harnesses(
    spec: str, detections: list[HarnessDetection]
) -> list[HarnessDetection]:
    by_id = {detection.id: detection for detection in detections}
    if spec == "auto":
        return [detection for detection in detections if detection.detected]
    if spec == "all":
        return detections
    requested = [item.strip() for item in spec.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(harness_ids()))
    if unknown:
        valid = ", ".join(harness_ids())
        raise SystemExit(
            f"Unknown client(s): {', '.join(unknown)}. Expected: auto, all, or one of {valid}."
        )
    return [by_id[item] for item in requested]


def apply_harness_setup(
    detection: HarnessDetection,
    *,
    repo_root: Path,
    catalog: Path | None = None,
    backend: str | None = None,
    server_name: str = "skillroute",
    mode: str = "prompt",
    yes: bool = False,
    install_mode: str = "mcp",
    server_source: str | None = None,
    scope: str | None = None,
) -> SetupResult:
    if mode == "0":
        return SetupResult(detection.id, "skipped", "client setup disabled")
    payload = build_harness_setup(
        harness=detection.id,
        mode=install_mode,
        repo_root=repo_root,
        catalog=catalog,
        backend=backend,
        server_name=server_name,
        server_source=server_source,
        scope=scope,
    )
    if payload["setup_method"] == "print_only":
        return SetupResult(
            detection.id, "printed", json.dumps(payload["config"], indent=2, sort_keys=True)
        )
    if mode == "prompt" and not yes and not confirm(f"Set up {detection.name} for SkillRoute"):
        return SetupResult(detection.id, "skipped", "user skipped setup")
    if payload["setup_method"] == "command":
        if not detection.detected:
            return SetupResult(detection.id, "skipped", detection.reason)
        command_parts = list(payload["install_command_parts"])
        # Prefer the binary detection actually found: VS Code may only have
        # `code-insiders`, and Homebrew installs land outside a bare name.
        if detection.command:
            command_parts[0] = detection.command
        subprocess.run(command_parts, check=True)
        return SetupResult(detection.id, "configured", payload["install_command"])
    if detection.config_path is None:
        return SetupResult(detection.id, "skipped", "no config path available")
    backup_path = merge_json_config(Path(detection.config_path), payload["config"])
    return SetupResult(
        detection.id,
        "configured",
        f"wrote {detection.config_path}",
        backup_path=str(backup_path) if backup_path else None,
    )


def merge_json_config(path: Path, incoming: dict[str, Any]) -> Path | None:
    import datetime as dt

    path = path.expanduser()
    existing: dict[str, Any] = {}
    backup_path: Path | None = None
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(existing, dict):
            raise ValueError(f"Existing config is not a JSON object: {path}")
        timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S")
        backup_path = path.with_name(f"{path.name}.bak-{timestamp}")
        shutil.copy2(path, backup_path)

    for key, value in incoming.items():
        if isinstance(value, dict):
            target = existing.setdefault(key, {})
            if not isinstance(target, dict):
                # ValueError, not TypeError: this is malformed on-disk data, not a caller bug
                raise ValueError(f"Existing config field is not an object: {key}")  # noqa: TRY004
            target.update(value)
        else:
            existing[key] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return backup_path


def confirm(prompt: str) -> bool:
    try:
        tty = open("/dev/tty", "r+", encoding="utf-8")  # noqa: SIM115 -- closed by `with tty:` below
    except OSError as exc:
        raise SystemExit(
            "No terminal is available for prompts. Re-run with --yes or SKILLROUTE_CLIENT_SETUP=1."
        ) from exc
    with tty:
        tty.write(f"? {prompt} [y/N] ")
        tty.flush()
        reply = tty.readline().strip().lower()
    return reply in {"y", "yes"}


def can_prompt() -> bool:
    if os.isatty(0):
        return True
    try:
        with open("/dev/tty", "r+", encoding="utf-8"):
            return True
    except OSError:
        return False


def print_detection_summary(detections: list[HarnessDetection]) -> None:
    print("Detected agent clients:")
    for detection in detections:
        marker = "found" if detection.detected else "missing"
        print(f"- {detection.name}: {marker} ({detection.reason})")


def run_setup_command(args: argparse.Namespace) -> None:
    env = HarnessEnvironment()
    detections = detect_harnesses(env)
    print_detection_summary(detections)
    selected = select_harnesses(args.clients, detections)
    if not selected:
        print("No clients selected for setup.")
        return
    if args.mode == "prompt" and not args.yes and not can_prompt():
        print(NO_TTY_SETUP_MESSAGE)
        return
    repo_root = args.repo_root.expanduser().resolve()
    catalog = args.catalog.expanduser().resolve() if args.catalog else default_catalog_path(repo_root)
    for detection in selected:
        if args.clients == "auto" and not detection.detected:
            continue
        result = apply_harness_setup(
            detection,
            repo_root=repo_root,
            catalog=catalog,
            backend=args.backend,
            yes=args.yes,
            mode=args.mode,
        )
        print(f"{detection.name}: {result.status} - {result.message}")
        if result.backup_path:
            print(f"{detection.name}: backup - {result.backup_path}")


def run_detect_command(args: argparse.Namespace) -> None:
    detections = detect_harnesses(HarnessEnvironment())
    if args.as_json:
        print(json.dumps([asdict(detection) for detection in detections], indent=2, sort_keys=True))
        return
    print_detection_summary(detections)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m skillroute.harness_setup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    detect_parser = subparsers.add_parser("detect", help="Detect supported harnesses")
    detect_parser.add_argument("--json", action="store_true", dest="as_json")
    detect_parser.set_defaults(func=run_detect_command)

    setup_parser = subparsers.add_parser("setup", help="Detect and optionally configure harnesses")
    setup_parser.add_argument("--repo-root", type=Path, required=True)
    setup_parser.add_argument("--catalog", type=Path, default=None)
    setup_parser.add_argument("--backend", default=None)
    setup_parser.add_argument("--clients", default="auto")
    setup_parser.add_argument("--mode", choices=SETUP_CHOICES, default="prompt")
    setup_parser.add_argument("--yes", action="store_true")
    setup_parser.set_defaults(func=run_setup_command)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
