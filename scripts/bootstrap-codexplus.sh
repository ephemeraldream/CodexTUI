#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
INSTALL_CODEX=ask
RUN_LOGIN=ask
LOGIN_METHOD=browser
INSTALL_SHIM=no
CHECK_ONLY=no
PYTHON_BIN=${PYTHON:-}
CXP_BIN=${CXP_BIN:-}
CODEX_BIN=${CODEX_REAL_BIN:-}
PERSIST_CODEX_BIN=no

usage() {
  cat <<'EOF'
Usage: scripts/bootstrap-codexplus.sh [options]

Set up CodexPlus from a checkout and verify that the official Codex CLI is ready.

Options:
  --yes                 Use the recommended setup path: install Codex if missing and run login if needed.
  --codex-bin PATH      Use and remember an existing Codex executable outside the standard locations.
  --install-codex       Install Codex CLI with the official standalone installer if codex is missing.
  --no-install-codex    Do not prompt to install Codex CLI.
  --login               Run Codex login if Codex is not already authenticated.
  --no-login            Do not prompt for Codex login.
  --with-api-key        Login with OPENAI_API_KEY through `codex login --with-api-key`.
  --install-shim        Install the optional `codex` shim after CodexPlus is installed.
  --check-only          Do not install anything; run environment checks only.
  -h, --help            Show this help.

Examples:
  scripts/bootstrap-codexplus.sh
  scripts/bootstrap-codexplus.sh --yes
  scripts/bootstrap-codexplus.sh --codex-bin ~/.local/bin/codex
  scripts/bootstrap-codexplus.sh --install-codex --login
  OPENAI_API_KEY=... scripts/bootstrap-codexplus.sh --with-api-key
EOF
}

log() {
  printf '%s\n' "$*"
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

prompt_yes() {
  question=$1
  default=${2:-no}
  if [ ! -t 0 ]; then
    [ "$default" = yes ]
    return
  fi
  if [ "$default" = yes ]; then
    suffix='[Y/n]'
  else
    suffix='[y/N]'
  fi
  printf '%s %s ' "$question" "$suffix" >&2
  read -r answer || answer=
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
    n|N|no|NO) return 1 ;;
    '') [ "$default" = yes ] ;;
    *) return 1 ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes)
      INSTALL_CODEX=yes
      RUN_LOGIN=yes
      ;;
    --codex-bin)
      shift
      [ "$#" -gt 0 ] || fail "--codex-bin needs a path."
      CODEX_BIN=$1
      PERSIST_CODEX_BIN=yes
      ;;
    --codex-bin=*)
      CODEX_BIN=${1#--codex-bin=}
      [ -n "$CODEX_BIN" ] || fail "--codex-bin needs a path."
      PERSIST_CODEX_BIN=yes
      ;;
    --install-codex)
      INSTALL_CODEX=yes
      ;;
    --no-install-codex)
      INSTALL_CODEX=no
      ;;
    --login)
      RUN_LOGIN=yes
      ;;
    --no-login)
      RUN_LOGIN=no
      ;;
    --with-api-key)
      RUN_LOGIN=yes
      LOGIN_METHOD=api-key
      ;;
    --install-shim)
      INSTALL_SHIM=yes
      ;;
    --check-only)
      CHECK_ONLY=yes
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
  shift
done

if [ "$CHECK_ONLY" = yes ]; then
  [ "$INSTALL_CODEX" = ask ] && INSTALL_CODEX=no
  [ "$RUN_LOGIN" = ask ] && RUN_LOGIN=no
fi

find_python() {
  if [ -n "$PYTHON_BIN" ]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "configured Python not found: $PYTHON_BIN"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python3)
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python)
  else
    fail "Python 3.11 or newer is required."
  fi
}

check_python() {
  "$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required.")
print("Python", ".".join(map(str, sys.version_info[:3])))
PY
}

expand_user_path() {
  case "$1" in
    "~")
      printf '%s\n' "$HOME"
      ;;
    "~/"*)
      printf '%s/%s\n' "$HOME" "${1#~/}"
      ;;
    *)
      printf '%s\n' "$1"
      ;;
  esac
}

refresh_codex_path() {
  if [ -x "$HOME/.codex/packages/standalone/current/bin/codex" ]; then
    export PATH="$HOME/.codex/packages/standalone/current/bin:$PATH"
  fi
}

resolve_codex() {
  refresh_codex_path
  if [ -n "$CODEX_BIN" ]; then
    CODEX_BIN=$(expand_user_path "$CODEX_BIN")
    [ -x "$CODEX_BIN" ] || fail "configured Codex executable is not executable: $CODEX_BIN"
    export CODEX_REAL_BIN="$CODEX_BIN"
    return 0
  fi
  if command -v codex >/dev/null 2>&1; then
    CODEX_BIN=$(command -v codex)
    export CODEX_REAL_BIN="$CODEX_BIN"
    return 0
  fi
  return 1
}

persist_codex_bin_if_requested() {
  [ "$PERSIST_CODEX_BIN" = yes ] || return 0
  [ "$CHECK_ONLY" = yes ] && return 0
  [ -n "$CODEX_BIN" ] || return 0
  "$PYTHON_BIN" - "$CODEX_BIN" <<'PY'
import json
import os
import sys
from pathlib import Path

codex_bin = str(Path(sys.argv[1]).expanduser().resolve(strict=False))
configured = os.environ.get("CODEXPLUS_CONFIG_HOME")
if configured:
    config_dir = Path(configured).expanduser()
else:
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "codexplus"
config_dir.mkdir(parents=True, exist_ok=True)
config_path = config_dir / "config.json"
try:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    payload = {}
if not isinstance(payload, dict):
    payload = {}
payload["codex_bin"] = codex_bin
config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"Saved Codex path in {config_path}")
PY
}

install_codex_if_needed() {
  if resolve_codex; then
    log "Codex CLI found: $CODEX_BIN"
    "$CODEX_BIN" --version || true
    persist_codex_bin_if_requested
    return
  fi

  warn "Codex CLI was not found on PATH."
  log "Official standalone install command:"
  log "  curl -fsSL https://chatgpt.com/codex/install.sh | sh"
  log "Alternative official options include npm and Homebrew when available:"
  log "  npm install -g @openai/codex"
  log "  brew install --cask codex"

  if [ "$CHECK_ONLY" = yes ]; then
    warn "Continuing because --check-only was requested."
    return
  fi

  should_install=no
  if [ "$INSTALL_CODEX" = yes ]; then
    should_install=yes
  elif [ "$INSTALL_CODEX" = ask ] && prompt_yes "Install Codex CLI now with the official standalone installer?" no; then
    should_install=yes
  fi

  if [ "$should_install" != yes ]; then
    fail "Install and activate Codex first, then rerun this script."
  fi

  command -v curl >/dev/null 2>&1 || fail "curl is required for the standalone Codex installer."
  curl -fsSL https://chatgpt.com/codex/install.sh | sh
  CODEX_BIN=
  resolve_codex || fail "Codex installer completed, but codex is still not on PATH."
  "$CODEX_BIN" --version || true
}

login_codex_if_needed() {
  resolve_codex || return
  if "$CODEX_BIN" login status >/dev/null 2>&1; then
    log "Codex login status: ready"
    return
  fi

  warn "Codex is installed but not logged in."
  if [ "$RUN_LOGIN" = no ]; then
    warn "Skipping Codex login. History browsing may work, but streaming needs authentication."
    return
  fi

  if [ "$LOGIN_METHOD" = api-key ]; then
    [ -n "${OPENAI_API_KEY:-}" ] || fail "OPENAI_API_KEY is required with --with-api-key."
    printf '%s\n' "$OPENAI_API_KEY" | "$CODEX_BIN" login --with-api-key
    return
  fi

  should_login=no
  if [ "$RUN_LOGIN" = yes ]; then
    should_login=yes
  elif [ "$RUN_LOGIN" = ask ] && prompt_yes "Run `codex login` now?" yes; then
    should_login=yes
  fi

  if [ "$should_login" = yes ]; then
    "$CODEX_BIN" login
  else
    warn "Skipping Codex login. Run `codex login` before using `cxp stream` or TUI prompts."
  fi
}

install_codexplus() {
  if [ "$CHECK_ONLY" = yes ]; then
    return
  fi

  if command -v pipx >/dev/null 2>&1; then
    log "Installing CodexPlus with pipx from $ROOT_DIR"
    pipx install -e "$ROOT_DIR" --force
    if command -v cxp >/dev/null 2>&1; then
      CXP_BIN=$(command -v cxp)
    elif [ -x "$HOME/.local/bin/cxp" ]; then
      CXP_BIN="$HOME/.local/bin/cxp"
    fi
    [ -n "$CXP_BIN" ] || fail "pipx installed CodexPlus, but cxp was not found on PATH."
    return
  fi

  log "pipx not found. Installing CodexPlus into $ROOT_DIR/.venv"
  "$PYTHON_BIN" -m venv "$ROOT_DIR/.venv"
  "$ROOT_DIR/.venv/bin/python" -m pip install -e "$ROOT_DIR"
  CXP_BIN="$ROOT_DIR/.venv/bin/cxp"
}

run_cxp() {
  if [ -n "$CXP_BIN" ] && [ -x "$CXP_BIN" ]; then
    "$CXP_BIN" "$@"
  elif [ -d "$ROOT_DIR/src/codex_plus" ]; then
    PYTHONPATH="$ROOT_DIR/src" "$PYTHON_BIN" -m codex_plus "$@"
  elif command -v cxp >/dev/null 2>&1; then
    cxp "$@"
  else
    fail "cxp is not installed and local source package was not found."
  fi
}

install_shim_if_requested() {
  [ "$INSTALL_SHIM" = yes ] || return 0
  if [ -n "$CODEX_BIN" ]; then
    run_cxp install-shim --real-codex "$CODEX_BIN" --force
  else
    run_cxp install-shim --force
  fi
}

run_doctor() {
  if [ -n "$CODEX_BIN" ]; then
    run_cxp doctor --codex-bin "$CODEX_BIN"
  else
    run_cxp doctor
  fi
}

next_command() {
  if [ -n "$CXP_BIN" ] && [ -x "$CXP_BIN" ]; then
    printf '%s tui\n' "$CXP_BIN"
  else
    printf 'cxp tui\n'
  fi
}

main() {
  log "CodexPlus bootstrap"
  log "Repository: $ROOT_DIR"
  find_python
  check_python
  install_codex_if_needed
  login_codex_if_needed
  install_codexplus
  install_shim_if_requested
  log ""
  run_doctor
  log "Next: run \`$(next_command)\` for the CodexPlus terminal UI."
}

main
