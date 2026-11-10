"""跑同一组测试。改前预期失败，改后预期通过。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def code_version(snapshot: str) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / snapshot)
    completed = subprocess.run(
        [sys.executable, "-c", "import items; print(items.CODE_VERSION)"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def run_items_tests(snapshot: str) -> tuple[int, str]:
    version = code_version(snapshot)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / snapshot)
    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_items.py",
        "-v",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    shown = "python -m unittest discover -s tests -p test_items.py -v"
    log = (
        f"command: PYTHONPATH={snapshot} {shown}\n"
        f"code_version: {version}\n"
        f"{completed.stdout}"
        f"{completed.stderr}"
        f"exit_code: {completed.returncode}\n"
    )
    return completed.returncode, log
