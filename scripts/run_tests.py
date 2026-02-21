# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unified pytest runner for public and CI test execution."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Final

import pytest

ROOT:Final = Path(__file__).resolve().parent.parent
TEMP:Final = ROOT / ".temp"

PROFILE_CONFIGS:Final[dict[str, tuple[str | None, str]]] = {
    "test": (None, "auto"),
    "utest": ("not itest and not smoke", "auto"),
    "itest": ("itest and not smoke", "0"),
    "smoke": ("smoke", "auto"),
}


def _append_verbosity(pytest_args:list[str], verbosity:int) -> None:
    if verbosity == 0:
        pytest_args.append("-q")
    else:
        pytest_args.append("-" + ("v" * verbosity))
        pytest_args.extend([
            "--durations=25",
            "--durations-min=0.5",
        ])


def _pytest_base_args(*, workers:str, verbosity:int) -> list[str]:
    # IMPORTANT: `-o addopts=...` replaces [tool.pytest.ini_options].addopts, it does not append.
    # Keep this option list in sync with pyproject addopts defaults when those change.
    pytest_args = [
        "-o",
        f"addopts=--strict-markers --doctest-modules -n {workers}",
        "--cov=src/kleinanzeigen_bot",
    ]
    _append_verbosity(pytest_args, verbosity)
    return pytest_args


def _resolve_path(path:Path) -> Path:
    if path.is_absolute():
        return path
    return ROOT / path


def _display_path(path:Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _cleanup_coverage_artifacts() -> None:
    TEMP.mkdir(parents = True, exist_ok = True)
    for pattern in ("coverage-*.xml", ".coverage-*.sqlite"):
        for stale_file in TEMP.glob(pattern):
            stale_file.unlink()

    for stale_path in (TEMP / "coverage.sqlite", ROOT / ".coverage"):
        if stale_path.exists():
            stale_path.unlink()


def _run_profile(*, profile:str, verbosity:int, passthrough:list[str]) -> int:
    marker, workers = PROFILE_CONFIGS[profile]
    pytest_args = _pytest_base_args(workers = workers, verbosity = verbosity)
    pytest_args.append("--cov-report=term-missing")

    if marker is not None:
        pytest_args.extend(["-m", marker])

    pytest_args.extend(passthrough)
    return pytest.main(pytest_args)


def _run_ci(*, marker:str, coverage_file:Path, xml_file:Path, workers:str, verbosity:int, passthrough:list[str]) -> int:
    resolved_coverage_file = _resolve_path(coverage_file)
    resolved_xml_file = _resolve_path(xml_file)
    resolved_coverage_file.parent.mkdir(parents = True, exist_ok = True)
    resolved_xml_file.parent.mkdir(parents = True, exist_ok = True)

    previous_coverage_file = os.environ.get("COVERAGE_FILE")
    os.environ["COVERAGE_FILE"] = str(resolved_coverage_file)

    pytest_args = _pytest_base_args(workers = workers, verbosity = verbosity)
    pytest_args.extend([
        "-m",
        marker,
        "--cov-report=term-missing",
        f"--cov-report=xml:{_display_path(resolved_xml_file)}",
    ])
    pytest_args.extend(passthrough)
    try:
        return pytest.main(pytest_args)
    finally:
        if previous_coverage_file is None:
            os.environ.pop("COVERAGE_FILE", None)
        else:
            os.environ["COVERAGE_FILE"] = previous_coverage_file


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description = "Run project tests")
    subparsers = parser.add_subparsers(dest = "command", required = True)

    run_parser = subparsers.add_parser("run", help = "Run tests for a predefined profile")
    run_parser.add_argument("profile", choices = sorted(PROFILE_CONFIGS))
    run_parser.add_argument("-v", "--verbose", action = "count", default = 0)

    subparsers.add_parser("ci-prepare", help = "Clean stale coverage artifacts")

    ci_run_parser = subparsers.add_parser("ci-run", help = "Run tests with explicit coverage outputs")
    ci_run_parser.add_argument("--marker", required = True)
    ci_run_parser.add_argument("--coverage-file", type = Path, required = True)
    ci_run_parser.add_argument("--xml-file", type = Path, required = True)
    ci_run_parser.add_argument("-n", "--workers", default = "auto")
    ci_run_parser.add_argument("-v", "--verbose", action = "count", default = 0)

    return parser


def main(argv:list[str] | None = None) -> int:
    os.chdir(ROOT)
    effective_argv = sys.argv[1:] if argv is None else argv

    parser = _build_parser()
    args, passthrough = parser.parse_known_args(effective_argv)

    if args.command == "run":
        return _run_profile(profile = args.profile, verbosity = args.verbose, passthrough = passthrough)

    if args.command == "ci-prepare":
        _cleanup_coverage_artifacts()
        return 0

    if args.command == "ci-run":
        return _run_ci(
            marker = args.marker,
            coverage_file = args.coverage_file,
            xml_file = args.xml_file,
            workers = str(args.workers),
            verbosity = args.verbose,
            passthrough = passthrough,
        )

    raise AssertionError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
