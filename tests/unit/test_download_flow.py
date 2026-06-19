# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for download flow functionality."""

import logging
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import download_flow
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.published_ads import PublishedAdsFetchIncompleteError
from kleinanzeigen_bot.utils import xdg_paths


class TestDownloadFlow:
    """Tests for download flow methods."""

    @pytest.mark.asyncio
    async def test_download_ads_passes_download_dir_and_published_ads(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Ensure download_ads wires resolved download_dir and published_ads_by_id into AdExtractor."""
        config_file = tmp_path / "config.yaml"
        test_bot.workspace = xdg_paths.Workspace.for_config(config_file, "kleinanzeigen-bot")
        test_bot.config_file_path = str(config_file)
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = [])

        mock_published_ads = [{"id": 123, "buyNowEligible": True}, {"id": 456, "buyNowEligible": False}]

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = mock_published_ads),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock) as mock_extractor,
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        # Verify published_ads_by_id is built correctly and passed to extractor
        mock_extractor.assert_called_once_with(
            test_bot.browser,
            test_bot.config,
            test_bot.workspace.download_dir,
            published_ads_by_id = {123: mock_published_ads[0], 456: mock_published_ads[1]},
        )

    def test_resolve_download_dir_uses_workspace_default_for_literal_default(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        config_file = tmp_path / "config.yaml"
        test_bot.workspace = xdg_paths.Workspace.for_config(config_file, "kleinanzeigen-bot")
        test_bot.config_file_path = str(config_file)
        test_bot.browser = cast(Any, None)

        assert download_flow.resolve_download_dir(test_bot.config, test_bot.config_file_path, test_bot.workspace) == test_bot.workspace.download_dir

    def test_resolve_download_dir_uses_config_relative_path(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        config_file = tmp_path / "config.yaml"
        test_bot.workspace = xdg_paths.Workspace.for_config(config_file, "kleinanzeigen-bot")
        test_bot.config_file_path = str(config_file)
        test_bot.browser = cast(Any, None)
        test_bot.config.download.dir = "./my-ads"

        assert download_flow.resolve_download_dir(test_bot.config, test_bot.config_file_path, test_bot.workspace) == (tmp_path / "my-ads").resolve()

    def test_resolve_download_dir_uses_absolute_path(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        config_file = tmp_path / "config.yaml"
        test_bot.workspace = xdg_paths.Workspace.for_config(config_file, "kleinanzeigen-bot")
        test_bot.config_file_path = str(config_file)
        test_bot.browser = cast(Any, None)
        test_bot.config.download.dir = str((tmp_path / "absolute-target").resolve())

        assert download_flow.resolve_download_dir(test_bot.config, test_bot.config_file_path, test_bot.workspace) == (tmp_path / "absolute-target").resolve()

    @pytest.mark.asyncio
    async def test_download_ads_uses_configured_relative_download_dir(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        config_file = tmp_path / "config.yaml"
        test_bot.workspace = xdg_paths.Workspace.for_config(config_file, "kleinanzeigen-bot")
        test_bot.config_file_path = str(config_file)
        test_bot.config.download.dir = "./ads"
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = [])

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock) as mock_extractor,
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        mock_extractor.assert_called_once()
        assert mock_extractor.call_args.args[2] == (tmp_path / "ads").resolve()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scenario",
        [
            {
                "published_ads": [{"id": 123, "state": "active"}],
                "expected_active": True,
            },
            {
                "published_ads": [{"id": 999, "state": "active"}],
                "expected_active": False,
            },
            {
                "published_ads": [{"id": 123, "state": "inactive"}],
                "expected_active": False,
            },
            {
                "published_ads": [{"id": 123, "state": "paused"}],
                "expected_active": False,
            },
        ],
    )
    async def test_download_ads_numeric_selector_resolves_and_passes_active_state(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        scenario:dict[str, Any],
    ) -> None:
        published_ads = scenario["published_ads"]
        expected_active = scenario["expected_active"]

        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "123"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = published_ads) as mock_fetch_published_ads,
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        mock_fetch_published_ads.assert_awaited_once_with(test_bot, test_bot.root_url, strict = True)

        extractor_mock.download_ad.assert_awaited_once_with(123, active = expected_active)

    @pytest.mark.asyncio
    async def test_download_ads_numeric_selector_fails_when_published_ads_fetch_incomplete(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "123"
        test_bot.browser = MagicMock()

        with (
            patch(
                "kleinanzeigen_bot.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                side_effect = PublishedAdsFetchIncompleteError("incomplete fetch"),
            ),
            patch("kleinanzeigen_bot.extract.AdExtractor") as mock_extractor,
            pytest.raises(PublishedAdsFetchIncompleteError, match = "incomplete fetch"),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        mock_extractor.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_ads_all_selector_uses_tolerant_published_ads_fetch(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = [])

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []) as mock_fetch_published_ads,
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        mock_fetch_published_ads.assert_awaited_once_with(test_bot, test_bot.root_url, strict = False)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scenario",
        [
            {
                "name": "active ad",
                "published_ads": [{"id": 123, "state": "active"}],
                "expected_active": True,
                "expect_ownership_warning": False,
            },
            {
                "name": "inactive ad",
                "published_ads": [{"id": 123, "state": "inactive"}],
                "expected_active": False,
                "expect_ownership_warning": False,
            },
            {
                "name": "paused ad",
                "published_ads": [{"id": 123, "state": "paused"}],
                "expected_active": False,
                "expect_ownership_warning": False,
            },
            {
                "name": "ad not in published profile",
                "published_ads": [{"id": 999, "state": "active"}],  # Different ID
                "expected_active": False,
                "expect_ownership_warning": True,
            },
        ],
        ids = lambda s: s["name"],
    )
    async def test_download_ads_all_selector_resolves_and_passes_active_state(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        caplog:pytest.LogCaptureFixture,
        scenario:dict[str, Any],
    ) -> None:
        """Test that --ads=all resolves and passes correct active state to download_ad."""
        # Setup
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test-ad/123-234-5678"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 123)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        caplog.set_level(logging.WARNING)

        with (
            patch(
                "kleinanzeigen_bot.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                return_value = scenario["published_ads"],
            ) as mock_fetch_published_ads,
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        # Verify published ads fetched with strict=False (tolerant mode for "all")
        mock_fetch_published_ads.assert_awaited_once_with(test_bot, test_bot.root_url, strict = False)

        # Verify download_ad called with correct active parameter
        extractor_mock.download_ad.assert_awaited_once_with(123, active = scenario["expected_active"])

        # Verify ownership warning only when expected
        ownership_warnings = [msg for msg in caplog.messages if "found in overview but not in published profile" in msg]
        if scenario["expect_ownership_warning"]:
            assert len(ownership_warnings) == 1
        else:
            assert len(ownership_warnings) == 0

    @pytest.mark.asyncio
    async def test_download_ads_all_selector_skips_invalid_ad_id(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
    ) -> None:
        """Test that --ads=all skips ads with invalid URL parsing (ad_id=-1)."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test/invalid-url"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = -1)  # URL parsing failed
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        # Verify download_ad was NOT called for invalid ad_id
        extractor_mock.download_ad.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scenario",
        [
            {
                "name": "new active ad",
                "published_ads": [{"id": 999, "state": "active"}],
                "saved_ad_ids": [123, 456],  # 999 is not saved, so it's "new"
                "expected_active": True,
            },
            {
                "name": "new inactive ad",
                "published_ads": [{"id": 999, "state": "inactive"}],
                "saved_ad_ids": [123, 456],  # 999 is not saved, so it's "new"
                "expected_active": False,
            },
            {
                "name": "new paused ad",
                "published_ads": [{"id": 999, "state": "paused"}],
                "saved_ad_ids": [123, 456],  # 999 is not saved, so it's "new"
                "expected_active": False,
            },
        ],
        ids = lambda s: s["name"],
    )
    async def test_download_ads_new_selector_resolves_and_passes_active_state(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        scenario:dict[str, Any],
    ) -> None:
        """Test that --ads=new resolves and passes correct active state to download_ad."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "new"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test-ad/999-234-5678"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 999)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        # Mock load_ads to return the saved_ad_ids
        saved_ads:list[tuple[str, MagicMock, dict[str, Any]]] = [
            (
                f"ad_{ad_id}.yaml",
                MagicMock(spec = Ad, id = ad_id),
                {},
            )
            for ad_id in scenario["saved_ad_ids"]
        ]

        with (
            patch(
                "kleinanzeigen_bot.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                return_value = scenario["published_ads"],
            ) as mock_fetch_published_ads,
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
            patch.object(test_bot, "load_ads", return_value = saved_ads),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        # Verify published ads fetched with strict=False (tolerant mode for "new")
        mock_fetch_published_ads.assert_awaited_once_with(test_bot, test_bot.root_url, strict = False)

        # Verify download_ad called with correct active parameter
        extractor_mock.download_ad.assert_awaited_once_with(999, active = scenario["expected_active"])

    @pytest.mark.asyncio
    async def test_download_ads_new_selector_skips_already_saved(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
    ) -> None:
        """Test that --ads=new skips already-saved ads (existing behavior unchanged)."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "new"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test-ad/123-234-5678"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 123)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        # Mock load_ads to return ad 123 as already saved
        saved_ads:list[tuple[str, MagicMock, dict[str, Any]]] = [("ad_123.yaml", MagicMock(spec = Ad, id = 123), {})]

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
            patch.object(test_bot, "load_ads", return_value = saved_ads),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        # Verify download_ad was NOT called for already-saved ad
        extractor_mock.download_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_ads_new_selector_skips_invalid_ad_id(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
    ) -> None:
        """Test that --ads=new skips ads with invalid URL parsing (ad_id=-1)."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "new"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test/invalid-url"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = -1)  # URL parsing failed
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
            patch.object(test_bot, "load_ads", return_value = []),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        # Verify download_ad was NOT called for invalid ad_id
        extractor_mock.download_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_ads_new_selector_passes_inactive_for_ad_not_in_published_profile(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
    ) -> None:
        """Test that --ads=new passes active=False when ad is not in published profile."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "new"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test-ad/999-234-5678"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 999)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        # Mock load_ads to return different saved ads (999 is new but not in published profile)
        saved_ads:list[tuple[str, MagicMock, dict[str, Any]]] = [("ad_123.yaml", MagicMock(spec = Ad, id = 123), {})]

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
            patch.object(test_bot, "load_ads", return_value = saved_ads),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        # Verify download_ad was called with active=False (not in profile)
        extractor_mock.download_ad.assert_awaited_once_with(999, active = False)

    @pytest.mark.asyncio
    async def test_download_ads_all_selector_skips_when_navigation_fails(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
    ) -> None:
        """Test that --ads=all skips download when navigate_to_ad_page returns False."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test/123"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 123)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = False)  # Navigation fails
        extractor_mock.download_ad = AsyncMock()

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", AsyncMock(return_value = [{"id": 123, "state": "active"}])),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        # Verify download_ad was NOT called when navigation fails
        extractor_mock.download_ad.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state_value", ["", "deleted", "expired", "pending", "draft", None])
    async def test_download_ads_all_selector_treats_unexpected_states_as_inactive(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        state_value:str | None,
    ) -> None:
        """Test that unexpected state values are treated as inactive."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        published_ads = [{"id": 123, "state": state_value}] if state_value is not None else [{"id": 123}]

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test/123"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 123)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", AsyncMock(return_value = published_ads)),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await download_flow.download_ads(
                web = test_bot, config = test_bot.config,
                config_file_path = test_bot.config_file_path,
                workspace = test_bot.workspace,
                ads_selector = test_bot.ads_selector,
                load_ads_func = test_bot.load_ads,
                root_url = test_bot.root_url,
            )

        # All non-"active" states should result in active=False
        extractor_mock.download_ad.assert_awaited_once_with(123, active = False)
