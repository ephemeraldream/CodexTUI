from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .paths import codex_home, real_codex_bin
from .store import CodexStore


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    status: str
    detail: str
    remedy: str = ""

    def to_json(self) -> dict[str, str]:
        payload = {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }
        if self.remedy:
            payload["remedy"] = self.remedy
        return payload


def run_diagnostics(*, codex_bin: Path | None = None) -> list[DiagnosticCheck]:
    binary = codex_bin or real_codex_bin()
    checks = [
        check_python(),
        check_ctui_version(),
        check_codex_binary(binary),
    ]
    if binary.exists():
        checks.extend(
            [
                check_codex_version(binary),
                check_codex_login(binary),
                check_codex_exec_json(binary),
            ]
        )
    checks.extend(
        [
            check_codex_state(),
            check_fzf(),
            check_ctui_on_path(),
        ]
    )
    return checks


def check_python() -> DiagnosticCheck:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 11):
        return DiagnosticCheck("python", "ok", f"Python {version}")
    return DiagnosticCheck(
        "python",
        "fail",
        f"Python {version}",
        "Install Python 3.11 or newer.",
    )


def check_ctui_version() -> DiagnosticCheck:
    return DiagnosticCheck("codextui", "ok", f"CodexTUI {__version__}")


def check_codex_binary(binary: Path) -> DiagnosticCheck:
    if binary.exists():
        return DiagnosticCheck("codex binary", "ok", str(binary))
    return DiagnosticCheck(
        "codex binary",
        "fail",
        f"Codex CLI not found at {binary}",
        "Install Codex, set CODEX_REAL_BIN, or run `scripts/bootstrap-codextui.sh --codex-bin PATH` from a checkout.",
    )


def check_codex_version(binary: Path) -> DiagnosticCheck:
    result = run_command([str(binary), "--version"])
    if result.returncode == 0:
        return DiagnosticCheck("codex version", "ok", first_line(result.stdout) or "version command succeeded")
    return DiagnosticCheck(
        "codex version",
        "fail",
        command_summary(result),
        "Verify the Codex executable works with `codex --version`.",
    )


def check_codex_login(binary: Path) -> DiagnosticCheck:
    result = run_command([str(binary), "login", "status"])
    if result.returncode == 0:
        return DiagnosticCheck("codex login", "ok", first_line(result.stdout) or "logged in")
    return DiagnosticCheck(
        "codex login",
        "warn",
        command_summary(result),
        "Run `codex login` for browser sign-in or `printenv OPENAI_API_KEY | codex login --with-api-key`.",
    )


def check_codex_exec_json(binary: Path) -> DiagnosticCheck:
    result = run_command([str(binary), "exec", "--help"])
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode == 0 and "--json" in output and "resume" in output:
        return DiagnosticCheck("codex exec json", "ok", "`codex exec --json` and `codex exec resume` are available")
    return DiagnosticCheck(
        "codex exec json",
        "fail",
        command_summary(result),
        "Update Codex so CodexTUI can stream `codex exec --json` events.",
    )


def check_codex_state() -> DiagnosticCheck:
    home = codex_home()
    dbs = sorted(home.glob("state_*.sqlite"))
    sessions = home / "sessions"
    try:
        thread_count = len(CodexStore(home).load_threads(limit=1))
    except Exception as exc:  # pragma: no cover - defensive against third party state files
        return DiagnosticCheck("codex history", "warn", f"unable to scan local Codex history under {home}: {exc}")
    if thread_count:
        if dbs:
            return DiagnosticCheck(
                "codex history",
                "ok",
                f"loaded recent history with {len(dbs)} state database(s) present in {home}",
            )
        return DiagnosticCheck("codex history", "ok", f"found session files in {sessions}")
    if dbs:
        return DiagnosticCheck(
            "codex history",
            "warn",
            f"found {len(dbs)} state database(s) in {home} but no readable sessions were found",
        )
    if sessions.exists():
        return DiagnosticCheck("codex history", "warn", f"{sessions} exists but no sessions were found")
    return DiagnosticCheck(
        "codex history",
        "warn",
        f"no local Codex history found under {home}",
        "Run Codex once to create local history. `ctui stream` can still start a new Codex turn after login.",
    )


def check_fzf() -> DiagnosticCheck:
    fzf = shutil.which("fzf")
    if fzf:
        return DiagnosticCheck("fzf", "ok", fzf)
    return DiagnosticCheck(
        "fzf",
        "warn",
        "fzf not found",
        "`ctui tui` works without fzf, but `ctui h` is better with fzf installed.",
    )


def check_ctui_on_path() -> DiagnosticCheck:
    ctui = shutil.which("ctui")
    if ctui:
        return DiagnosticCheck("ctui on PATH", "ok", ctui)
    return DiagnosticCheck(
        "ctui on PATH",
        "warn",
        "ctui command is not on PATH",
        "Install with `pipx install -e .` or use `PYTHONPATH=src python3 -m codex_tui` from the checkout.",
    )


def render_diagnostics(checks: list[DiagnosticCheck]) -> str:
    width = max(len(check.name) for check in checks) if checks else 12
    lines = ["CodexTUI doctor"]
    for check in checks:
        status = check.status.upper()
        lines.append(f"{status:5}  {check.name:{width}}  {check.detail}")
        if check.remedy:
            lines.append(f"{'':5}  {'':{width}}  fix: {check.remedy}")
    failures = sum(1 for check in checks if check.status == "fail")
    warnings = sum(1 for check in checks if check.status == "warn")
    lines.append("")
    lines.append(f"Summary: {failures} failure(s), {warnings} warning(s).")
    return "\n".join(lines) + "\n"


def diagnostics_json(checks: list[DiagnosticCheck]) -> str:
    return json.dumps([check.to_json() for check in checks], ensure_ascii=False, indent=2) + "\n"


def diagnostics_exit_code(checks: list[DiagnosticCheck], *, strict: bool = False) -> int:
    if any(check.status == "fail" for check in checks):
        return 1
    if strict and any(check.status == "warn" for check in checks):
        return 1
    return 0


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else "command timed out"
        return subprocess.CompletedProcess(command, 124, stdout, stderr)


def first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def command_summary(result: subprocess.CompletedProcess[str]) -> str:
    detail = first_line(result.stderr) or first_line(result.stdout) or "command failed"
    return f"exit {result.returncode}: {detail}"
