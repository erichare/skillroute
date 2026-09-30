#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${SKILLROUTE_REPO_URL:-https://github.com/jestatsio/skillroute.git}"
REF="${SKILLROUTE_REF:-main}"
INSTALL_DIR="${SKILLROUTE_INSTALL_DIR:-}"
ASSUME_YES="${SKILLROUTE_ASSUME_YES:-0}"
CLIENT_SETUP="${SKILLROUTE_CLIENT_SETUP:-prompt}"
CLIENTS="${SKILLROUTE_CLIENTS:-auto}"
INDEX_LOCAL_SKILLS="${SKILLROUTE_INDEX_LOCAL_SKILLS:-prompt}"

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  BLUE="$(printf '\033[38;5;27m')"
  CYAN="$(printf '\033[36m')"
  GREEN="$(printf '\033[32m')"
  YELLOW="$(printf '\033[33m')"
  BOLD="$(printf '\033[1m')"
  RESET="$(printf '\033[0m')"
else
  BLUE=""
  CYAN=""
  GREEN=""
  YELLOW=""
  BOLD=""
  RESET=""
fi

usage() {
  cat <<'EOF'
SkillRoute installer

Usage:
  scripts/install.sh [options]
  curl -fsSL https://raw.githubusercontent.com/jestatsio/skillroute/main/scripts/install.sh | bash

Options:
  --yes                 Accept prompts, including detected client setup.
  --install-dir PATH    Install or update SkillRoute at PATH.
  --repo URL            Git repository URL.
  --ref REF             Git branch, tag, or ref. Defaults to main.
  --clients SPEC        auto, all, or comma-separated client ids.
  --no-client-setup     Detect clients but do not configure them.
  --help                Show this help.

Environment:
  SKILLROUTE_INSTALL_DIR
  SKILLROUTE_REPO_URL
  SKILLROUTE_REF
  SKILLROUTE_ASSUME_YES=1
  SKILLROUTE_CLIENT_SETUP=prompt|0|1
  SKILLROUTE_CLIENTS=auto|all|ibm-bob,codex,claude-code,claude-desktop,vscode,windsurf,cursor
  SKILLROUTE_INDEX_LOCAL_SKILLS=0|1|prompt
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)
      ASSUME_YES=1
      shift
      ;;
    --install-dir)
      if [[ $# -lt 2 ]]; then
        echo "--install-dir requires a path" >&2
        exit 1
      fi
      INSTALL_DIR="$2"
      shift 2
      ;;
    --repo)
      if [[ $# -lt 2 ]]; then
        echo "--repo requires a URL" >&2
        exit 1
      fi
      REPO_URL="$2"
      shift 2
      ;;
    --ref)
      if [[ $# -lt 2 ]]; then
        echo "--ref requires a branch, tag, or ref" >&2
        exit 1
      fi
      REF="$2"
      shift 2
      ;;
    --clients)
      if [[ $# -lt 2 ]]; then
        echo "--clients requires auto, all, or a comma-separated client list" >&2
        exit 1
      fi
      CLIENTS="$2"
      shift 2
      ;;
    --no-client-setup)
      CLIENT_SETUP=0
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

is_truthy() {
  case "$1" in
    1|true|TRUE|yes|YES|y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

say() {
  printf '%b\n' "$*"
}

banner() {
  say "${BLUE}${BOLD}"
  cat <<'EOF'
SkillRoute
Local-first skill routing for agent builders
EOF
  say "${RESET}"
}

step() {
  say ""
  say "${CYAN}${BOLD}==>${RESET} ${BOLD}$1${RESET}"
}

ok() {
  say "${GREEN}[ok]${RESET} $1"
}

warn() {
  say "${YELLOW}[warn]${RESET} $1"
}

die() {
  say "${YELLOW}[error]${RESET} $1" >&2
  exit 1
}

confirm() {
  local prompt="$1"
  local reply
  if is_truthy "$ASSUME_YES"; then
    say "${GREEN}[yes]${RESET} $prompt"
    return 0
  fi
  if [[ ! -r /dev/tty ]]; then
    die "No terminal is available for prompts. Re-run with --yes or SKILLROUTE_ASSUME_YES=1."
  fi
  printf '%b? %s [y/N] %b' "$BOLD" "$prompt" "$RESET" > /dev/tty
  read -r reply < /dev/tty || return 1
  case "$reply" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

should_do() {
  local setting="$1"
  local prompt="$2"
  case "$setting" in
    1|true|TRUE|yes|YES|y|Y) return 0 ;;
    0|false|FALSE|no|NO|n|N) return 1 ;;
    *) confirm "$prompt" ;;
  esac
}

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    die "Missing required command: $1"
  fi
}

run_confirmed() {
  local label="$1"
  shift
  step "$label"
  if confirm "$label"; then
    "$@"
    ok "$label"
  else
    warn "Skipped: $label"
  fi
}

local_checkout_root() {
  local source_path="${BASH_SOURCE[0]:-}"
  local candidate
  if [[ -n "$source_path" && -f "$source_path" ]]; then
    candidate="$(cd "$(dirname "$source_path")/.." && pwd)"
    if [[ -f "$candidate/pyproject.toml" && -d "$candidate/mcp" ]]; then
      printf '%s\n' "$candidate"
    fi
  fi
}

ensure_clean_checkout() {
  local dir="$1"
  if ! git -C "$dir" diff --quiet || ! git -C "$dir" diff --cached --quiet; then
    die "Existing checkout has local changes: $dir"
  fi
}

prepare_checkout() {
  local detected_root="$1"
  local using_current="$2"

  step "Prepare SkillRoute checkout"
  if [[ "$using_current" == "1" ]]; then
    ok "Using current checkout: $detected_root"
    INSTALL_DIR="$detected_root"
    return
  fi

  if [[ -d "$INSTALL_DIR/.git" ]]; then
    if confirm "Update existing checkout at $INSTALL_DIR from $REF"; then
      ensure_clean_checkout "$INSTALL_DIR"
      git -C "$INSTALL_DIR" fetch origin "$REF"
      git -C "$INSTALL_DIR" checkout "$REF" 2>/dev/null || git -C "$INSTALL_DIR" checkout -t "origin/$REF"
      git -C "$INSTALL_DIR" pull --ff-only origin "$REF"
      ok "Updated checkout: $INSTALL_DIR"
    else
      warn "Using existing checkout without updating: $INSTALL_DIR"
    fi
    return
  fi

  if [[ -e "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]]; then
    die "Install path exists and is not an empty directory: $INSTALL_DIR"
  fi

  if confirm "Clone SkillRoute into $INSTALL_DIR"; then
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --branch "$REF" "$REPO_URL" "$INSTALL_DIR"
    ok "Cloned SkillRoute: $INSTALL_DIR"
  else
    die "Cannot continue without a SkillRoute checkout."
  fi
}

run_client_setup() {
  local setup_args
  setup_args=(
    "python"
    "-m"
    "skillroute.client_setup"
    "setup"
    "--repo-root"
    "$INSTALL_DIR"
    "--catalog"
    "$INSTALL_DIR/.skillroute/catalog.db"
    "--clients"
    "$CLIENTS"
    "--mode"
    "$CLIENT_SETUP"
  )
  if is_truthy "$ASSUME_YES"; then
    setup_args+=("--yes")
  fi
  uv run "${setup_args[@]}"
}

main() {
  local detected_root
  local using_current=0

  banner
  say "SkillRoute by JEStats (https://jestats.io). Sets up SkillRoute and detects agent clients for MCP setup."
  say "Repository: $REPO_URL"
  say "Ref:        $REF"
  say "Clients:    $CLIENTS"

  detected_root="$(local_checkout_root || true)"
  if [[ -z "$INSTALL_DIR" && -n "$detected_root" ]]; then
    INSTALL_DIR="$detected_root"
    using_current=1
  fi
  if [[ -z "$INSTALL_DIR" ]]; then
    # The repo was renamed skill-route -> skillroute. Keep using an existing
    # checkout at the old path rather than cloning a second copy: installs from
    # before the rename have generated MCP configs pointing into it, and those
    # would silently keep running the stale clone.
    if [[ -d "$HOME/.skillroute/skill-route" ]]; then
      INSTALL_DIR="$HOME/.skillroute/skill-route"
    else
      INSTALL_DIR="$HOME/.skillroute/skillroute"
    fi
  fi
  say "Install:    $INSTALL_DIR"

  step "Check prerequisites"
  confirm "Check required commands: git, uv, node, npm" || die "Cannot continue without checking prerequisites."
  need_command git
  need_command uv
  need_command node
  need_command npm
  ok "Required commands are available"

  prepare_checkout "$detected_root" "$using_current"
  cd "$INSTALL_DIR"

  run_confirmed "Install Python environment with uv" uv sync --extra dev
  run_confirmed "Install MCP server Node dependencies" npm --prefix mcp ci
  run_confirmed "Build MCP server" npm --prefix mcp run build
  run_confirmed "Install Skill Atlas web dependencies" npm --prefix web ci
  run_confirmed "Build Skill Atlas web UI" npm --prefix web run build
  run_confirmed "Index example skills" uv run skillroute index --root examples/skills

  step "Index local skill roots"
  if should_do "$INDEX_LOCAL_SKILLS" "Index local Codex and agent skill roots if found"; then
    uv run skillroute dogfood index
    ok "Local skill-root indexing complete"
  else
    warn "Skipped local skill-root indexing"
  fi

  run_confirmed "Check local retrieval backend" uv run skillroute backend status --backend local

  step "Detect and set up agent clients"
  run_client_setup

  say ""
  say "${BLUE}${BOLD}SkillRoute is ready.${RESET}"
  say "Try:"
  say "  cd $INSTALL_DIR"
  say "  uv run skillroute route \"Build an MCP server that exposes routing tools\""
  say "  uv run skillroute ui"
  say ""
  say "Manual client setup:"
  say "  uv run skillroute mcp config --client ibm-bob"
  say "  uv run skillroute mcp config --client codex"
  say "  uv run skillroute mcp config --client claude-code"
  say "  uv run skillroute mcp config --client claude-desktop"
  say "  uv run skillroute mcp config --client vscode"
  say "  uv run skillroute mcp config --client windsurf"
  say "  uv run skillroute mcp config --client cursor"
}

main "$@"
