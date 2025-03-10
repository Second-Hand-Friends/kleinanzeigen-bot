#!/usr/bin/env python3
"""Utility functions for git operations."""

import subprocess
from collections.abc import Sequence


def get_modified_python_files() -> Sequence[str]:
    """Get list of modified Python files from git.

    Returns:
        Sequence[str]: List of modified Python file paths
    """
    git_cmd = ['git', 'diff', '--name-only', '--diff-filter=ACMR', 'HEAD']
    result = subprocess.run(git_cmd, capture_output=True, text=True, check=True)
    if not result.stdout.strip():
        return []
    return [f for f in result.stdout.splitlines() if f.endswith('.py')]
