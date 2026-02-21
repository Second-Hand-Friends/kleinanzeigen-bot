# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unified pytest runner with quiet defaults and verbose opt-in."""
from __future__ import annotations

import argparse
import sys

import pytest


def _parse_args(argv:list[str]) -> tuple[int, list[str]]:
    parser = argparse.ArgumentParser(add_help = False)
    parser.add_argument("-v", "--verbose", action = "count", default = 0)
    args, passthrough = parser.parse_known_args(argv)
    return args.verbose, passthrough


def main(argv:list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    verbosity, passthrough = _parse_args(effective_argv)

    pytest_args = [
        "-o",
        "addopts=--strict-markers --doctest-modules -n auto",
        "--cov=src/kleinanzeigen_bot",
        "--cov-report=term-missing",
    ]

    if verbosity == 0:
        pytest_args.append("-q")
    else:
        pytest_args.append("-" + ("v" * verbosity))
        pytest_args.extend([
            "--durations=25",
            "--durations-min=0.5",
        ])

    pytest_args.extend(passthrough)
    return pytest.main(pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
