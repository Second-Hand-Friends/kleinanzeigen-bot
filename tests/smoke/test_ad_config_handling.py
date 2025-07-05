# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

import pytest

from tests.conftest import SmokeKleinanzeigenBot


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_ad_config_can_be_processed(smoke_bot:SmokeKleinanzeigenBot) -> None:
    """Smoke test: bot can process a simple ad config without raising exceptions."""
    try:
        await smoke_bot.run(["publish"])
    except Exception as e:
        pytest.fail(f"Bot failed to process basic config: {e}")
