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
        # Explicitly delete stack frames to prevent reference cycles and potential memory leaks.
        # inspect.stack() returns FrameInfo objects that contain references to frame objects,
        # which can create circular references. While Python's GC handles this, explicit cleanup
        # is recommended per Python docs: https://docs.python.org/3/library/inspect.html#the-interpreter-stack
        # codeql[py/unnecessary-delete]: Intentional cleanup to avoid reference cycles with frame objects
        del stack


def is_integer(obj:Any) -> bool:
    try:
        int(obj)
        return True
    except (ValueError, TypeError):
        return False
