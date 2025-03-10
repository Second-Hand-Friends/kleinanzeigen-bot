#!/usr/bin/env python3
"""Helper script to format Python code using isort and autopep8."""

import subprocess, sys

from scripts.git_utils import get_modified_python_files


def format_files() -> int:
    """Format Python files using isort and autopep8.

    Returns:
        int: 0 on success, non-zero on failure
    """
    try:
        # Format imports in modified files
        py_files = get_modified_python_files()
        if py_files:
            subprocess.run(['isort', *py_files], check=True)

        # Format all files with autopep8
        subprocess.run(
            ['autopep8', '--recursive', '--in-place', 'src', 'tests', '--verbose'],
            check=True
        )
        return 0
    except subprocess.CalledProcessError as e:
        print(f"Error during formatting: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(format_files())
