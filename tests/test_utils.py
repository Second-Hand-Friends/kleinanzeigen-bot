"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import os, sys, time
from kleinanzeigen_bot import utils


def test_pause():
    start = time.time()
    utils.pause(100, 100)
    elapsed = 1000 * (time.time() - start)
    if sys.platform == "darwin" and os.getenv("GITHUB_ACTIONS", "true") == "true":
        assert 99 < elapsed < 300
    else:
        assert 99 < elapsed < 110
