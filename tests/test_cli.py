from __future__ import annotations

import os
import subprocess
import sys
import unittest


class CliTests(unittest.TestCase):
    def test_version_command(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-m", "codex_plus", "--version"],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("CodexPlus 0.1.0", result.stdout)

    def test_help_hides_internal_preview_commands(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-m", "codex_plus", "--help"],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("files", result.stdout)
        self.assertNotIn("==SUPPRESS==", result.stdout)
        self.assertNotIn("file-preview", result.stdout)

    def test_compress_placeholder_is_explicitly_not_implemented(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-m", "codex_plus", "compress"],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("not implemented", result.stdout)


if __name__ == "__main__":
    unittest.main()
