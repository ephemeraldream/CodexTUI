from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bootstrap-codexplus.sh"


class BootstrapScriptTests(unittest.TestCase):
    def test_bootstrap_script_has_valid_shell_syntax(self) -> None:
        result = subprocess.run(["sh", "-n", str(SCRIPT)], text=True, capture_output=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bootstrap_script_help_is_available(self) -> None:
        result = subprocess.run([str(SCRIPT), "--help"], text=True, capture_output=True, check=False)

        self.assertEqual(result.returncode, 0)
        self.assertIn("--yes", result.stdout)
        self.assertIn("--codex-bin", result.stdout)
        self.assertIn("--install-codex", result.stdout)
        self.assertIn("--with-api-key", result.stdout)

    def test_bootstrap_can_persist_custom_codex_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex = write_fake_codex(root)
            bin_dir = root / "fake-tools"
            bin_dir.mkdir()
            write_fake_pipx(bin_dir)
            write_fake_cxp(bin_dir)
            config_home = root / "config"
            env = dict(os.environ)
            env["CODEXPLUS_CONFIG_HOME"] = str(config_home)
            env["CODEX_HOME"] = str(root / "codex-home")
            env["PYTHON"] = sys.executable
            env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            result = subprocess.run(
                [str(SCRIPT), "--no-install-codex", "--no-login", "--codex-bin", str(codex)],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads((config_home / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["codex_bin"], str(codex.resolve(strict=False)))
            self.assertIn("Saved Codex path", result.stdout)


def write_fake_codex(root: Path) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text(
        """#!/bin/sh
case "${1:-}" in
  --version)
    echo "codex-cli 9.9.9"
    exit 0
    ;;
  login)
    if [ "${2:-}" = "status" ]; then
      echo "logged in"
      exit 0
    fi
    exit 0
    ;;
  exec)
    if [ "${2:-}" = "--help" ]; then
      echo "Usage: codex exec [OPTIONS] [PROMPT]"
      echo "Commands: resume"
      echo "Options: --json"
      exit 0
    fi
    exit 0
    ;;
esac
exit 2
""",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    return codex


def write_fake_pipx(bin_dir: Path) -> None:
    pipx = bin_dir / "pipx"
    pipx.write_text(
        """#!/bin/sh
echo "fake pipx $*"
exit 0
""",
        encoding="utf-8",
    )
    pipx.chmod(0o755)


def write_fake_cxp(bin_dir: Path) -> None:
    cxp = bin_dir / "cxp"
    cxp.write_text(
        """#!/bin/sh
case "${1:-}" in
  doctor)
    echo "fake doctor"
    exit 0
    ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    cxp.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
