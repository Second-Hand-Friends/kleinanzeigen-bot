"""Utility helpers for the coverage pipeline used by the pdm test scripts."""
# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import argparse
import os
import subprocess  # noqa: S404 subprocess usage is limited to known internal binaries
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMP = ROOT / ".temp"


def prepare() -> None:
    TEMP.mkdir(parents=True, exist_ok=True)
    for pattern in ("coverage-*.xml", ".coverage-*.sqlite"):
        for coverage_file in TEMP.glob(pattern):
            coverage_file.unlink()
    for path in (TEMP / "coverage.sqlite", ROOT / ".coverage"):
        if path.exists():
            path.unlink()


def run_suite(data_file: Path, xml_file: Path, marker: str) -> None:
    os.environ["COVERAGE_FILE"] = str(ROOT / data_file)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--capture=tee-sys",
        "-m",
        marker,
        "--cov=src/kleinanzeigen_bot",
        f"--cov-report=xml:{ROOT / xml_file}",
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)  # noqa: S603 arguments are constant and controlled


def combine(data_files: list[Path]) -> None:
    combined = TEMP / "coverage.sqlite"
    os.environ["COVERAGE_FILE"] = str(combined)
    cmd = [sys.executable, "-m", "coverage", "combine"] + [str(ROOT / data) for data in data_files]
    subprocess.run(cmd, cwd=ROOT, check=True)  # noqa: S603 arguments controlled by this script
    subprocess.run([sys.executable, "-m", "coverage", "report", "-m"], cwd=ROOT, check=True)  # noqa: S603


def main() -> None:
    parser = argparse.ArgumentParser(description = "Coverage helper commands")
    subparsers = parser.add_subparsers(dest = "command", required = True)

    subparsers.add_parser("prepare", help = "Clean coverage artifacts")

    run_parser = subparsers.add_parser("run", help = "Run pytest with a custom coverage file")
    run_parser.add_argument("data_file", type = Path, help = "Coverage data file to write")
    run_parser.add_argument("xml_file", type = Path, help = "XML report path")
    run_parser.add_argument("marker", help = "pytest marker expression")

    combine_parser = subparsers.add_parser("combine", help = "Combine coverage data files")
    combine_parser.add_argument(
        "data_files",
        nargs = "+",
        type = Path,
        help = "List of coverage data files to combine",
    )

    args = parser.parse_args()

    if args.command == "prepare":
        prepare()
    elif args.command == "run":
        run_suite(args.data_file, args.xml_file, args.marker)
    else:
        combine(args.data_files)


if __name__ == "__main__":
    main()
