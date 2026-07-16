from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def default_real_codex_bin() -> Path:
    return codex_home() / "packages" / "standalone" / "current" / "bin" / "codex"


def codexplus_config_dir() -> Path:
    configured = os.environ.get("CODEXPLUS_CONFIG_HOME")
    if configured:
        return Path(configured).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "codexplus"


def codexplus_config_path() -> Path:
    return codexplus_config_dir() / "config.json"


def configured_real_codex_bin() -> Path | None:
    path = codexplus_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    configured = raw.get("codex_bin")
    if not isinstance(configured, str) or not configured.strip():
        return None
    return Path(configured).expanduser()


def real_codex_bin() -> Path:
    configured = os.environ.get("CODEX_REAL_BIN")
    if configured:
        return Path(configured).expanduser()
    configured_path = configured_real_codex_bin()
    if configured_path is not None:
        return configured_path
    standalone = default_real_codex_bin()
    if standalone.exists():
        return standalone
    path_binary = shutil.which("codex")
    if path_binary:
        return Path(path_binary)
    return standalone
