from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import path_bootstrap  # noqa: F401

from codex_tui.doctor import diagnostics_exit_code, diagnostics_json, run_diagnostics
from codex_tui.paths import real_codex_bin


class DoctorTests(unittest.TestCase):
    def test_diagnostics_pass_with_working_codex_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex = write_fake_codex(Path(temp_dir), login_status=0)
            checks = run_diagnostics(codex_bin=codex)

        statuses = {check.name: check.status for check in checks}
        self.assertEqual(statuses["codex binary"], "ok")
        self.assertEqual(statuses["codex version"], "ok")
        self.assertEqual(statuses["codex login"], "ok")
        self.assertEqual(statuses["codex exec json"], "ok")
        self.assertEqual(diagnostics_exit_code(checks), 0)

        rows = json.loads(diagnostics_json(checks))
        self.assertTrue(any(row["name"] == "codex exec json" for row in rows))

    def test_missing_codex_binary_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checks = run_diagnostics(codex_bin=Path(temp_dir) / "missing-codex")

        statuses = {check.name: check.status for check in checks}
        self.assertEqual(statuses["codex binary"], "fail")
        self.assertEqual(diagnostics_exit_code(checks), 1)

    def test_login_warning_does_not_fail_unless_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex = write_fake_codex(Path(temp_dir), login_status=1)
            checks = run_diagnostics(codex_bin=codex)

        statuses = {check.name: check.status for check in checks}
        self.assertEqual(statuses["codex login"], "warn")
        self.assertEqual(diagnostics_exit_code(checks), 0)
        self.assertEqual(diagnostics_exit_code(checks, strict=True), 1)

    def test_cli_doctor_json_uses_supplied_codex_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex = write_fake_codex(Path(temp_dir), login_status=0)
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "doctor", "--json", "--codex-bin", str(codex)],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = json.loads(result.stdout)
        self.assertTrue(any(row["name"] == "codex version" and row["status"] == "ok" for row in rows))

    def test_cli_doctor_warns_when_state_database_has_no_readable_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "codex-home"
            home.mkdir()
            codex = write_fake_codex(root, login_status=0)
            con = sqlite3.connect(home / "state_5.sqlite")
            try:
                con.execute("CREATE TABLE unrelated (id TEXT)")
                con.commit()
            finally:
                con.close()

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "doctor", "--json", "--codex-bin", str(codex)],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = json.loads(result.stdout)
        history = next(row for row in rows if row["name"] == "codex history")
        self.assertEqual(history["status"], "warn")
        self.assertIn("no readable sessions", history["detail"])

    def test_real_codex_bin_falls_back_to_path_when_standalone_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex = write_fake_codex(root, login_status=0)
            env = {
                "CODEX_HOME": str(root / "codex-home"),
                "PATH": str(codex.parent),
            }
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(real_codex_bin(), codex)

    def test_real_codex_bin_uses_persisted_codextui_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex = write_fake_codex(root, login_status=0)
            config_home = root / "config"
            config_home.mkdir()
            (config_home / "config.json").write_text(
                json.dumps({"codex_bin": str(codex)}),
                encoding="utf-8",
            )
            env = {
                "CODEX_HOME": str(root / "codex-home"),
                "CODEXTUI_CONFIG_HOME": str(config_home),
                "PATH": "",
            }
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(real_codex_bin(), codex)


def write_fake_codex(root: Path, *, login_status: int) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text(
        f"""#!/bin/sh
case "${{1:-}}" in
  --version)
    echo "codex-cli 9.9.9"
    exit 0
    ;;
  login)
    if [ "${{2:-}}" = "status" ]; then
      echo "login status"
      exit {login_status}
    fi
    exit 0
    ;;
  exec)
    if [ "${{2:-}}" = "--help" ]; then
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


if __name__ == "__main__":
    unittest.main()
