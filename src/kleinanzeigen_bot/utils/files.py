# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import os


def abspath(relative_path:str, relative_to:str | None = None) -> str:
    """
    Makes a given relative path absolute based on another file/folder
    """
    if not relative_to:
        return os.path.abspath(relative_path)

    if os.path.isabs(relative_path):
        return relative_path

    if os.path.isfile(relative_to):
        relative_to = os.path.dirname(relative_to)

    return os.path.normpath(os.path.join(relative_to, relative_path))
