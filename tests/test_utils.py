"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import os, sys, time
import pytest
from kleinanzeigen_bot import utils


def test_ensure():
    utils.ensure(True, "TRUE")
    utils.ensure("Some Value", "TRUE")
    utils.ensure(123, "TRUE")
    utils.ensure(-123, "TRUE")
    utils.ensure(lambda: True, "TRUE")

    with pytest.raises(AssertionError):
        utils.ensure(False, "FALSE")

    with pytest.raises(AssertionError):
        utils.ensure(0, "FALSE")

    with pytest.raises(AssertionError):
        utils.ensure("", "FALSE")

    with pytest.raises(AssertionError):
        utils.ensure(None, "FALSE")

    with pytest.raises(AssertionError):
        utils.ensure(lambda: False, "FALSE", timeout = 2)


def test_pause():
    start = time.time()
    utils.pause(100, 100)
    elapsed = 1000 * (time.time() - start)
    if sys.platform == "darwin" and os.getenv("GITHUB_ACTIONS", "true") == "true":
        assert 99 < elapsed < 300
    else:
        assert 99 < elapsed < 120
