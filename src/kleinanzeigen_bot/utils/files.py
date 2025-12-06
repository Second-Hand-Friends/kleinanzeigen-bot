# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio, os  # isort: skip
from pathlib import Path


def abspath(relative_path:str, relative_to:str | None = None) -> str:
    """
    Return a normalized absolute path based on *relative_to*.

    If 'relative_path' is already absolute, it is normalized and returned.
    Otherwise, the function joins 'relative_path' with 'relative_to' (or the current working directory if not provided),
    normalizes the result, and returns the absolute path.
    """

    if not relative_to:
        return os.path.abspath(relative_path)

    if os.path.isabs(relative_path):
        return os.path.normpath(relative_path)

    base = os.path.abspath(relative_to)
    if os.path.isfile(base):
        base = os.path.dirname(base)

    return os.path.normpath(os.path.join(base, relative_path))


async def exists(path:str | Path) -> bool:
    """
    Asynchronously check if a file or directory exists.

    :param path: Path to check
    :return: True if path exists, False otherwise
    """
    return await asyncio.get_running_loop().run_in_executor(None, Path(path).exists)


async def is_dir(path:str | Path) -> bool:
    """
    Asynchronously check if a path is a directory.

    :param path: Path to check
    :return: True if path is a directory, False otherwise
    """
    return await asyncio.get_running_loop().run_in_executor(None, Path(path).is_dir)
