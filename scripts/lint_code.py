#!/usr/bin/env python3
"""Helper script to lint Python code using multiple tools."""

import subprocess, sys
from collections.abc import Sequence

from scripts.git_utils import get_modified_python_files


def run_tool(cmd: Sequence[str], tool_name: str) -> tuple[int, str]:
    """Run a lint tool and return its exit code and error message.

    Args:
        cmd: Command to run as list of strings
        tool_name: Name of the tool for error messages

    Returns:
        tuple[int, str]: tuple of (exit_code, error_message)
    """
    try:
        subprocess.run(list(cmd), check=True)
        return 0, ""
    except subprocess.CalledProcessError as e:
        return e.returncode, f"{tool_name} fehlgeschlagen\n"


def lint_files() -> int:
    """Run all linting tools.

    Returns:
        int: Number of errors encountered
    """
    errors = 0
    error_messages = []

    # Run isort on modified files
    py_files = get_modified_python_files()
    if py_files:
        code, msg = run_tool(['isort', *py_files], 'isort')
        errors += code
        if msg:
            error_messages.append(msg)

    # Run other linting tools
    tools = [
        (['pylint', '-v', 'src', 'tests'], 'pylint'),
        (['autopep8', '-v', '--in-place', '--recursive', 'src', 'tests'], 'autopep8'),
        (['mypy', 'src', 'tests'], 'mypy')
    ]

    for cmd, name in tools:
        code, msg = run_tool(cmd, name)
        errors += code
        if msg:
            error_messages.append(msg)

    # Print all error messages at the end
    if error_messages:
        print("\n".join(error_messages), file=sys.stderr)

    return errors


if __name__ == '__main__':
    sys.exit(lint_files())
