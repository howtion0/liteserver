"""Run pytest while mirroring failures into public GitHub check annotations."""

from __future__ import annotations

import subprocess
import sys


def _github_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = completed.stdout
    print(output, end="")
    if completed.returncode:
        # Check-run annotations remain readable without downloading private logs.
        print(f"::error title=pytest failed::{_github_escape(output[-50_000:])}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
