# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Terminal color gating for CLI output.

Provides a single pure function, :func:`should_use_color`, that
encapsulates the project's color policy:

* ``NO_COLOR`` environment variable present and non-empty → ``False``
* ``FORCE_COLOR`` environment variable present and non-empty → ``True``
* If both are set, ``NO_COLOR`` wins.
* Otherwise color is enabled only when *stream* is a TTY
  (``isatty()`` returns ``True``).

Empty-string environment variable values are treated as unset.
"""

from __future__ import annotations

import os
import sys
from typing import IO, Mapping

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def should_use_color(
    stream:IO[str] | None = None,
    env:Mapping[str, str] | None = None,
) -> bool:
    """Determine whether coloured terminal output should be used.

    Args:
        stream: The output stream to check for TTY status.
                ``None`` (default) reads ``sys.stdout`` at call time.
                Inject a fake stream for tests.
        env:    Environment-variable mapping. ``None`` (default) reads
                ``os.environ`` at call time. Inject for tests.

    Returns:
        ``True`` if color output is enabled per the policy above.
    """
    _stream:IO[str] = sys.stdout if stream is None else stream
    if env is None:
        env = os.environ

    no_color = env.get("NO_COLOR", "")
    force_color = env.get("FORCE_COLOR", "")

    # NO_COLOR wins when both are present and non-empty.
    if no_color:
        return False
    if force_color:
        return True

    # Fall back to TTY detection.
    try:
        return bool(_stream.isatty())
    except (OSError, AttributeError):
        return False
