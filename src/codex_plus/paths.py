from __future__ import annotations

import os
from pathlib import Path


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def default_real_codex_bin() -> Path:
    return codex_home() / "packages" / "standalone" / "current" / "bin" / "codex"


def real_codex_bin() -> Path:
    return Path(os.environ.get("CODEX_REAL_BIN", str(default_real_codex_bin()))).expanduser()
