"""Utility helpers for the coverage pipeline used by the pdm test scripts."""
# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import argparse
import logging
import os
import subprocess  # noqa: S404 subprocess usage is limited to known internal binaries
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMP = ROOT / ".temp"

logging.basicConfig(level = logging.INFO, format = "%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def prepare() -> None:
    logger.info("Preparing coverage artifacts in %s", TEMP)
    try:
        TEMP.mkdir(parents = True, exist_ok = True)
        removed_patterns = 0
        for pattern in ("coverage-*.xml", ".coverage-*.sqlite"):
            for coverage_file in TEMP.glob(pattern):
                coverage_file.unlink()
                removed_patterns += 1
        removed_paths = 0
        for path in (TEMP / "coverage.sqlite", ROOT / ".coverage"):
            if path.exists():
                path.unlink()
                removed_paths += 1
    except Exception as exc:  # noqa: S110 suppress to log
        logger.exception("Failed to clean coverage artifacts: %s", exc)
        raise
    logger.info(
        "Removed %d pattern-matching files and %d fixed paths during prepare",
        removed_patterns,
        removed_paths,
    )


def run_suite(data_file:Path, xml_file:Path, marker:str, extra_args:list[str]) -> None:
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
    if extra_args:
        cmd.extend(extra_args)
    logger.info("Running pytest marker=%s coverage_data=%s xml=%s", marker, data_file, xml_file)
    subprocess.run(cmd, cwd = ROOT, check = True)  # noqa: S603 arguments are constant and controlled
    logger.info("Pytest marker=%s finished", marker)


def combine(data_files:list[Path]) -> None:
    combined = TEMP / "coverage.sqlite"
    os.environ["COVERAGE_FILE"] = str(combined)
    resolved = []
    missing = []
    for data in data_files:
        candidate = ROOT / data
        if not candidate.exists():
            missing.append(str(candidate))
        else:
            resolved.append(candidate)
    if missing:
        message = f"Coverage data files missing: {', '.join(missing)}"
        logger.error(message)
        raise FileNotFoundError(message)
    cmd = [sys.executable, "-m", "coverage", "combine"] + [str(path) for path in resolved]
    logger.info("Combining coverage data files: %s", ", ".join(str(path) for path in resolved))
    subprocess.run(cmd, cwd = ROOT, check = True)  # noqa: S603 arguments controlled by this script
    logger.info("Coverage combine completed, generating report")
    subprocess.run([sys.executable, "-m", "coverage", "report", "-m"], cwd = ROOT, check = True)  # noqa: S603


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

    args, extra_args = parser.parse_known_args()

    if args.command == "prepare":
        prepare()
    elif args.command == "run":
        run_suite(args.data_file, args.xml_file, args.marker, extra_args)
    else:
        combine(args.data_files)


if __name__ == "__main__":
    main()
