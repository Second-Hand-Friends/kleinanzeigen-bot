"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import pytest
from kleinanzeigen_bot import utils


def test_ensure() -> None:
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
