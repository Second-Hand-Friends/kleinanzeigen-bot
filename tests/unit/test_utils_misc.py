# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import pytest

from kleinanzeigen_bot.utils import misc


def test_ensure() -> None:
    misc.ensure(True, "TRUE")
    misc.ensure("Some Value", "TRUE")
    misc.ensure(123, "TRUE")
    misc.ensure(-123, "TRUE")
    misc.ensure(lambda: True, "TRUE")

    with pytest.raises(AssertionError):
        misc.ensure(False, "FALSE")

    with pytest.raises(AssertionError):
        misc.ensure(0, "FALSE")

    with pytest.raises(AssertionError):
        misc.ensure("", "FALSE")

    with pytest.raises(AssertionError):
        misc.ensure(None, "FALSE")

    with pytest.raises(AssertionError):
        misc.ensure(lambda: False, "FALSE", timeout = 2)
