# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import inspect
from typing import Any


def get_caller(depth:int = 1) -> inspect.FrameInfo | None:
    stack = inspect.stack()
    try:
        for frame in stack[depth + 1:]:
            if frame.function and frame.function != "<lambda>":
                return frame
        return None
    finally:
        del stack  # Clean up the stack to avoid reference cycles


def is_integer(obj:Any) -> bool:
    try:
        int(obj)
        return True
    except (ValueError, TypeError):
        return False
