# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import kleinanzeigen_bot.extract as extract_module
from kleinanzeigen_bot.model.ad_model import AdPartial
from kleinanzeigen_bot.model.config_model import Config


@pytest.fixture
def extractor(browser_mock: MagicMock, test_bot_config: Config) -> extract_module.AdExtractor:
    return extract_module.AdExtractor(browser=browser_mock, config=test_bot_config, download_dir=Path("downloaded-ads"))


@pytest.mark.asyncio
async def test_directory_handling_avoids_deleting_colliding_title_folder(extractor: extract_module.AdExtractor, tmp_path: Path) -> None:
    base_dir = tmp_path / "downloaded-ads"
    base_dir.mkdir()
    extractor.config.download.folder_name_template = "{title}"
    extractor.config.download.ad_file_name_template = "ad_{id}"

    colliding_title_dir = base_dir / "Shared Title"
    colliding_title_dir.mkdir()
    foreign_yaml = colliding_title_dir / "ad_99999.yaml"
    foreign_yaml.write_text("foreign ad")
    expected_fallback_dir = base_dir / "ad_12345"

    ad_cfg = AdPartial.model_validate(
        {
            "title": "Shared Title",
            "description": "Test Description",
            "category": "Dienstleistungen",
            "price": 100,
            "images": [],
            "contact": {"name": "Test User", "street": "Test Street 123", "zipcode": "12345", "location": "Test City"},
        }
    )

    page_mock = MagicMock()
    page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
    extractor.page = page_mock

    with (
        patch.object(extractor, "_extract_title_from_ad_page", new_callable=AsyncMock, return_value="Shared Title"),
        patch.object(extractor, "_extract_ad_page_info", new_callable=AsyncMock, return_value=ad_cfg) as mock_extract,
    ):
        result_cfg, result_dir, ad_file_stem = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

        assert result_cfg == ad_cfg
        assert result_dir == expected_fallback_dir
        assert result_dir.exists()
        assert ad_file_stem == "ad_12345"
        assert colliding_title_dir.exists()
        assert foreign_yaml.exists()
        mock_extract.assert_awaited_once_with(str(expected_fallback_dir), 12345, "ad_12345", active=None)
