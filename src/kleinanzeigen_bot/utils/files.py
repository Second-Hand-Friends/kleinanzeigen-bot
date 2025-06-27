# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import os


def abspath(relative_path:str, relative_to:str | None = None) -> str:
    """Return an absolute path based on *relative_to*.
    # This function ensures that the returned path is always absolute, regardless of whether
    # the input 'relative_to' is absolute or relative. This is achieved by normalizing
    # 'relative_to' to an absolute path before joining it with 'relative_path'.
    """

    if not relative_to:
        return os.path.abspath(relative_path)

    if os.path.isabs(relative_path):
        return os.path.abspath(relative_path)

    base = os.path.abspath(relative_to)
    if os.path.isfile(base):
        base = os.path.dirname(base)

    return os.path.normpath(os.path.join(base, relative_path))
