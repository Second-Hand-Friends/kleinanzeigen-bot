# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

import pytest

from tests.conftest import SmokeKleinanzeigenBot


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_bot_runs_without_crashing(smoke_bot:SmokeKleinanzeigenBot) -> None:
    """Smoke test: bot.run() completes without raising exceptions."""
    try:
        await smoke_bot.run(["publish"])
    except Exception as e:
        pytest.fail(f"Smoke test failed: {e}")
