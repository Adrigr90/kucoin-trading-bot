"""``python -m kbot.ops`` on the server: the hardening check of F2-A10, as a report."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from kbot.ops import check
from kbot.ops.check import Command


def _run(command: list[str]) -> Command:
    """The command's output and exit code, kept apart: a failure never looks like an
    output (review of E4a, E4a-06)."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    except OSError as error:
        return Command("", 127, str(error))
    except subprocess.TimeoutExpired:
        return Command("", 124, "timeout")
    return Command(done.stdout, done.returncode, done.stderr)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="hardening check of the server (F2-A10)")
    parser.add_argument("--report", type=Path, help="write the report to this file too")
    arguments = parser.parse_args(argv)
    verdicts = check.run_all(_run, _read)
    text = check.report(verdicts, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    print(text, end="")
    if arguments.report is not None:
        arguments.report.write_text(text, encoding="utf-8")
    return 0 if all(v.ok for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
