# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import logging
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from kleinanzeigen_bot.ad_loading import (
    check_ad_changed,
    check_ad_republication,
    discover_ad_files,
    is_valid_ads_selector,
    load_ads,
    resolve_ad_category,
    resolve_ad_images,
    update_content_hashes,
)
from kleinanzeigen_bot.model.ad_model import Ad, AdPartial
from kleinanzeigen_bot.model.config_model import (
    Config,
)
from kleinanzeigen_bot.utils import dicts, misc

# --------------------------------------------------------------------------- #
# Local fixtures (base_ad_config is in tests/conftest.py)
# --------------------------------------------------------------------------- #


@pytest.fixture
def test_bot_config() -> Config:
    """Provides a basic sample configuration for testing."""
    return Config.model_validate({
        "ad_defaults": {
            "contact": {
                "name": "dummy_name",
                "zipcode": "12345"
            },
        },
        "login": {
            "username": "dummy_user",
            "password": "dummy_password"
        },
        "publishing": {
            "delete_old_ads": "BEFORE_PUBLISH",
            "delete_old_ads_by_title": False
        }
    })


# --------------------------------------------------------------------------- #
# discover_ad_files
# --------------------------------------------------------------------------- #


def test_discover_ad_files_no_matches(tmp_path:Path) -> None:
    """Globbing with no matching files returns an empty dict."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")
    result = discover_ad_files(str(config_file), ["nonexistent/*.yaml"])
    assert result == {}


def test_discover_ad_files_finds_matching(tmp_path:Path, base_ad_config:dict[str, Any]) -> None:
    """Globbing finds matching ad files and skips ad_fields.yaml."""
    ads_dir = tmp_path / "ads"
    ads_dir.mkdir()
    ad1 = ads_dir / "my_ad.yaml"
    dicts.save_dict(ad1, base_ad_config | {"title": "Ad 1"})
    ad2 = ads_dir / "other_ad.yaml"
    dicts.save_dict(ad2, base_ad_config | {"title": "Ad 2"})
    # ad_fields.yaml should be filtered out
    (ads_dir / "ad_fields.yaml").write_text("")

    config_file = tmp_path / "config.yaml"
    config_file.write_text("")

    result = discover_ad_files(str(config_file), ["ads/*.yaml"])
    assert len(result) == 2
    assert all(str(k).endswith(".yaml") for k in result)


# --------------------------------------------------------------------------- #
# is_valid_ads_selector
# --------------------------------------------------------------------------- #


class TestIsValidAdsSelector:
    """Focused tests for is_valid_ads_selector."""

    def test_single_keyword(self) -> None:
        assert is_valid_ads_selector("all", {"all", "new"})

    def test_keyword_list(self) -> None:
        assert is_valid_ads_selector("all,new", {"all", "new"})

    def test_numeric_ids(self) -> None:
        assert is_valid_ads_selector("123,456", {"all"})

    def test_mixed_keyword_and_numeric_rejected(self) -> None:
        assert not is_valid_ads_selector("all,123", {"all", "new"})

    def test_invalid_keyword(self) -> None:
        assert not is_valid_ads_selector("invalid", {"all", "new"})

    def test_whitespace_stripping_in_list(self) -> None:
        """Validation strips whitespace; ' all , new ' is valid."""
        assert is_valid_ads_selector(" all , new ", {"all", "new"})


# --------------------------------------------------------------------------- #
# check_ad_republication
# --------------------------------------------------------------------------- #


class TestCheckAdRepublication:
    """Focused tests for check_ad_republication."""

    def test_no_timestamps_returns_true(self, base_ad_config:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config)
        assert check_ad_republication(ad_cfg, "test.yaml") is True

    def test_recent_update_returns_false(self, base_ad_config:dict[str, Any]) -> None:
        now = misc.now()
        yesterday = now - timedelta(days = 1)
        ad_cfg = Ad.model_validate(base_ad_config | {
            "updated_on": yesterday.isoformat(),
            "republication_interval": 7,
        })
        assert check_ad_republication(ad_cfg, "test.yaml", now = now) is False

    def test_old_update_returns_true(self, base_ad_config:dict[str, Any]) -> None:
        now = misc.now()
        old = now - timedelta(days = 10)
        ad_cfg = Ad.model_validate(base_ad_config | {
            "updated_on": old.isoformat(),
            "republication_interval": 7,
        })
        assert check_ad_republication(ad_cfg, "test.yaml", now = now) is True


# --------------------------------------------------------------------------- #
# check_ad_changed
# --------------------------------------------------------------------------- #


class TestCheckAdChanged:
    """Focused tests for check_ad_changed."""

    def test_no_id_returns_false(self, base_ad_config:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config | {"id": None})
        ad_cfg_orig = base_ad_config | {"id": None}
        assert check_ad_changed(ad_cfg, ad_cfg_orig, "test.yaml") is False

    def test_no_stored_hash_returns_false(self, base_ad_config:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config | {"id": "12345"})
        ad_cfg_orig = base_ad_config | {"id": "12345"}
        # No content_hash key → stored_hash is None → falsy → returns False
        assert check_ad_changed(ad_cfg, ad_cfg_orig, "test.yaml") is False

    def test_changed_hash_returns_true_and_mutates(self, base_ad_config:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config | {"id": "12345"})
        ad_cfg_orig = base_ad_config | {"id": "12345", "description": "Original"}
        ad_cfg_orig["content_hash"] = "old_hash"
        result = check_ad_changed(ad_cfg, ad_cfg_orig, "test.yaml")
        assert result is True
        # Side effect: content_hash was updated
        assert ad_cfg_orig["content_hash"] != "old_hash"


# --------------------------------------------------------------------------- #
# resolve_ad_category
# --------------------------------------------------------------------------- #


class TestResolveAdCategory:
    """Focused tests for resolve_ad_category."""

    def test_known_alias_resolves(self, base_ad_config:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "garten"})
        categories = {"garten": "123"}
        resolve_ad_category(ad_cfg, categories)
        assert ad_cfg.category == "123"

    def test_unknown_without_arrow_unchanged(self, base_ad_config:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "unknown"})
        categories = {"garten": "123"}
        resolve_ad_category(ad_cfg, categories)
        assert ad_cfg.category == "unknown"

    def test_parent_fallback(self, base_ad_config:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "garten > blumen"})
        categories = {"garten": "123"}
        resolve_ad_category(ad_cfg, categories)
        assert ad_cfg.category == "123"

    def test_parent_fallback_unknown_parent_unchanged(self, base_ad_config:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "foo > bar"})
        categories = {"garten": "123"}
        resolve_ad_category(ad_cfg, categories)
        assert ad_cfg.category == "foo > bar"

    def test_no_category_unresolved_with_empty_categories(self, base_ad_config:dict[str, Any]) -> None:
        """When categories dict is empty, category is kept unchanged."""
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "some_category"})
        resolve_ad_category(ad_cfg, {})
        assert ad_cfg.category == "some_category"


# --------------------------------------------------------------------------- #
# resolve_ad_images
# --------------------------------------------------------------------------- #


class TestResolveAdImages:
    """Focused tests for resolve_ad_images."""

    def test_empty_patterns_returns_empty(self, tmp_path:Path) -> None:
        ad_file = tmp_path / "test.yaml"
        ad_file.write_text("")
        assert resolve_ad_images(str(ad_file), []) == []

    def test_missing_images_raises(self, tmp_path:Path) -> None:
        ad_file = tmp_path / "test.yaml"
        ad_file.write_text("")
        with pytest.raises(AssertionError, match = "No images found"):
            resolve_ad_images(str(ad_file), ["nonexistent/*.jpg"])

    def test_finds_matching_images(self, tmp_path:Path) -> None:
        ad_file = tmp_path / "test.yaml"
        ad_file.write_text("")
        (tmp_path / "photo1.jpg").write_text("")
        (tmp_path / "photo2.png").write_text("")

        result = resolve_ad_images(str(ad_file), ["*.jpg", "*.png"])
        assert len(result) == 2

    def test_unsupported_extension_raises(self, tmp_path:Path) -> None:
        ad_file = tmp_path / "test.yaml"
        ad_file.write_text("")
        (tmp_path / "photo.bmp").write_text("")

        with pytest.raises(AssertionError, match = "Unsupported image file type"):
            resolve_ad_images(str(ad_file), ["*.bmp"])


# --------------------------------------------------------------------------- #
# update_content_hashes
# --------------------------------------------------------------------------- #


class TestUpdateContentHashes:
    """Focused tests for update_content_hashes."""

    def test_counter_progression(
        self, base_ad_config:dict[str, Any], caplog:pytest.LogCaptureFixture
    ) -> None:
        """Display counter must advance for every ad, even when hash is unchanged."""
        ads = [
            _build_ad(base_ad_config, None, "Unchanged Ad 1"),
            _build_ad(base_ad_config, None, "Changed Ad"),
            _build_ad(base_ad_config, None, "Unchanged Ad 2"),
        ]

        # Pre-compute hashes from the raw config dict (matching the production code path)
        for _ad_file, _ad_cfg, ad_cfg_orig in ads:
            ad_cfg_orig["content_hash"] = AdPartial.model_validate(ad_cfg_orig).update_content_hash().content_hash

        # Make the middle ad's original hash differ
        ads[1][2]["content_hash"] = "deliberately_wrong_hash"

        with (
            caplog.at_level(logging.INFO),
            patch.object(dicts, "save_dict"),
        ):
            changed = update_content_hashes(ads)

        assert changed == 1

        processing = [r for r in caplog.records if r.message.startswith("Processing")]
        assert len(processing) == 3
        assert "1/3" in processing[0].message
        assert "2/3" in processing[1].message
        assert "3/3" in processing[2].message

        summary = [r for r in caplog.records if "DONE:" in r.message and "content_hash" in r.message]
        assert any("1 ad" in r.message for r in summary)


# --------------------------------------------------------------------------- #
# load_ads — validation error tests
# --------------------------------------------------------------------------- #


_VALIDATION_ERROR_CASES = [
    pytest.param(
        {"title": ""},
        "title",
        id = "missing_title",
    ),
    pytest.param(
        {"price_type": "INVALID_TYPE"},
        "price_type",
        id = "invalid_price_type",
    ),
    pytest.param(
        {"shipping_type": "INVALID_TYPE"},
        "shipping_type",
        id = "invalid_shipping_type",
    ),
    pytest.param(
        {"price_type": "GIVE_AWAY", "price": 100},
        "price",
        id = "invalid_price_config",
    ),
    pytest.param(
        {"price_type": "FIXED", "price": None},
        "price is required when price_type is FIXED",
        id = "missing_price",
    ),
]


@pytest.mark.parametrize(("ad_overrides", "expected_error"), _VALIDATION_ERROR_CASES)
def test_load_ads_validation_errors(
    tmp_path:Path,
    base_ad_config:dict[str, Any],
    test_bot_config:Config,
    ad_overrides:dict[str, Any],
    expected_error:str,
) -> None:
    """Invalid ad configurations raise ValidationError with field-specific messages."""
    ad_dir = tmp_path / "ads"
    ad_dir.mkdir()
    ad_file = ad_dir / "test_ad.yaml"
    dicts.save_dict(ad_file, base_ad_config | ad_overrides)

    config_file = tmp_path / "config.yaml"
    config_file.write_text("")

    with pytest.raises(ValidationError) as exc_info:
        load_ads(
            config_file_path = str(config_file),
            ad_file_patterns = ["ads/*.yaml"],
            ad_defaults = test_bot_config.ad_defaults,
            categories = {},
            ads_selector = "due",
            command = "publish",
        )
    assert expected_error in str(exc_info.value)


# --------------------------------------------------------------------------- #
# load_ads — selector behavior tests
# --------------------------------------------------------------------------- #


def test_load_ads_with_changed_selector(
    test_bot_config:Config, base_ad_config:dict[str, Any]
) -> None:
    """Only changed ads are loaded with the 'changed' selector."""
    ad_defaults = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "", "suffix": ""}}}).ad_defaults

    ad_cfg = Ad.model_validate(
        base_ad_config
        | {
            "id": "12345",
            "title": "Changed Ad",
            "updated_on": "2024-01-01T00:00:00",
            "created_on": "2024-01-01T00:00:00",
            "active": True,
        }
    )
    changed_ad = ad_cfg.model_dump()
    changed_hash = ad_cfg.update_content_hash().content_hash
    changed_ad["content_hash"] = changed_hash
    # Modify to simulate a change
    changed_ad["title"] = "Changed Ad - Modified"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        dicts.save_dict(ad_dir / "changed_ad.yaml", changed_ad)

        config_file = temp_path / "config.yaml"
        config_file.write_text("")

        with patch(
            "kleinanzeigen_bot.utils.dicts.load_dict",
            side_effect = [
                changed_ad,  # First call returns the changed ad
                {},  # Second call for ad_fields.yaml
            ],
        ):
            ads = load_ads(
                config_file_path = str(config_file),
                ad_file_patterns = ["ads/*.yaml"],
                ad_defaults = ad_defaults,
                categories = {},
                ads_selector = "changed",
                command = "publish",
            )
            assert len(ads) == 1
            assert ads[0][1].title == "Changed Ad - Modified"


def test_load_ads_with_due_selector_includes_all_due_ads(
    base_ad_config:dict[str, Any], test_bot_config:Config
) -> None:
    """'due' selector includes all ads due for republication, regardless of changes."""
    ad_defaults = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "", "suffix": ""}}}).ad_defaults

    current_time = misc.now()
    old_date = (current_time - timedelta(days = 10)).isoformat()

    ad_cfg = Ad.model_validate(
        base_ad_config
        | {
            "id": "12345",
            "title": "Changed Ad",
            "updated_on": old_date,
            "created_on": old_date,
            "republication_interval": 7,
            "active": True,
        }
    )
    changed_ad = ad_cfg.model_dump()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        dicts.save_dict(ad_dir / "changed_ad.yaml", changed_ad)

        config_file = temp_path / "config.yaml"
        config_file.write_text("")

        with patch(
            "kleinanzeigen_bot.utils.dicts.load_dict",
            side_effect = [
                changed_ad,
                {},
            ],
        ):
            ads = load_ads(
                config_file_path = str(config_file),
                ad_file_patterns = ["ads/*.yaml"],
                ad_defaults = ad_defaults,
                categories = {},
                ads_selector = "due",
                command = "publish",
            )
            assert len(ads) == 1


def test_load_ads_with_changed_selector_and_pending_price_reduction(
    test_bot_config:Config, base_ad_config:dict[str, Any]
) -> None:
    """'changed' selector also loads ads with pending auto price reductions (update mode)."""
    ad_defaults = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "", "suffix": ""}}}).ad_defaults

    ad_cfg = Ad.model_validate(
        base_ad_config
        | {
            "id": "12345",
            "title": "Ad With Price Reduction",
            "updated_on": "2024-01-01T00:00:00",
            "created_on": "2024-01-01T00:00:00",
            "price": 100,
            "price_reduction_count": 0,
            "repost_count": 1,
            "active": True,
            "auto_price_reduction": {
                "enabled": True,
                "on_update": True,
                "strategy": "FIXED",
                "amount": 10,
                "min_price": 1,
                "delay_days": 0,
                "delay_reposts": 0,
            },
        }
    )
    ad_dict = ad_cfg.model_dump()
    ad_dict["content_hash"] = ad_cfg.update_content_hash().content_hash

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        dicts.save_dict(ad_dir / "ad_with_reduction.yaml", ad_dict)

        config_file = temp_path / "config.yaml"
        config_file.write_text("")

        with patch(
            "kleinanzeigen_bot.utils.dicts.load_dict",
            side_effect = [
                ad_dict,
                {},
            ],
        ):
            ads = load_ads(
                config_file_path = str(config_file),
                ad_file_patterns = ["ads/*.yaml"],
                ad_defaults = ad_defaults,
                categories = {},
                ads_selector = "changed",
                command = "update",
            )
            assert len(ads) == 1
            assert ads[0][1].title == "Ad With Price Reduction"


def test_load_ads_with_changed_selector_no_price_reduction_when_not_configured(
    test_bot_config:Config, base_ad_config:dict[str, Any]
) -> None:
    """'changed' selector does not load an unchanged ad when auto_price_reduction is disabled."""
    ad_defaults = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "", "suffix": ""}}}).ad_defaults

    ad_cfg = Ad.model_validate(
        base_ad_config
        | {
            "id": "12345",
            "title": "Unchanged Ad",
            "updated_on": "2024-01-01T00:00:00",
            "created_on": "2024-01-01T00:00:00",
            "price": 100,
            "repost_count": 1,
            "active": True,
            "auto_price_reduction": {
                "enabled": False,
                "on_update": True,
                "strategy": "FIXED",
                "amount": 10,
                "min_price": 1,
                "delay_days": 0,
                "delay_reposts": 0,
            },
        }
    )
    ad_dict = ad_cfg.model_dump()
    ad_dict["content_hash"] = ad_cfg.update_content_hash().content_hash

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        dicts.save_dict(ad_dir / "unchanged_ad.yaml", ad_dict)

        config_file = temp_path / "config.yaml"
        config_file.write_text("")

        with patch(
            "kleinanzeigen_bot.utils.dicts.load_dict",
            side_effect = [
                ad_dict,
                {},
            ],
        ):
            ads = load_ads(
                config_file_path = str(config_file),
                ad_file_patterns = ["ads/*.yaml"],
                ad_defaults = ad_defaults,
                categories = {},
                ads_selector = "changed",
                command = "update",
            )
            assert len(ads) == 0


def test_load_ads_with_changed_selector_does_not_include_price_reduction_in_publish_mode(
    test_bot_config:Config, base_ad_config:dict[str, Any]
) -> None:
    """'changed' selector in publish mode skips unchanged ads even when price reduction is pending."""
    ad_defaults = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "", "suffix": ""}}}).ad_defaults

    ad_cfg = Ad.model_validate(
        base_ad_config
        | {
            "id": "12345",
            "title": "Ad With Price Reduction",
            "updated_on": "2024-01-01T00:00:00",
            "created_on": "2024-01-01T00:00:00",
            "price": 100,
            "price_reduction_count": 0,
            "repost_count": 1,
            "active": True,
            "auto_price_reduction": {
                "enabled": True,
                "on_update": True,
                "strategy": "FIXED",
                "amount": 10,
                "min_price": 1,
                "delay_days": 0,
                "delay_reposts": 0,
            },
        }
    )
    ad_dict = ad_cfg.model_dump()
    ad_dict["content_hash"] = ad_cfg.update_content_hash().content_hash

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        dicts.save_dict(ad_dir / "ad_with_reduction.yaml", ad_dict)

        config_file = temp_path / "config.yaml"
        config_file.write_text("")

        with patch(
            "kleinanzeigen_bot.utils.dicts.load_dict",
            side_effect = [
                ad_dict,
                {},
            ],
        ):
            ads = load_ads(
                config_file_path = str(config_file),
                ad_file_patterns = ["ads/*.yaml"],
                ad_defaults = ad_defaults,
                categories = {},
                ads_selector = "changed",
                command = "publish",
            )
            # Should NOT load — price reduction only applies in update mode
            assert len(ads) == 0


def test_load_ads_with_new_selector_excludes_already_published(
    base_ad_config:dict[str, Any], test_bot_config:Config
) -> None:
    """'new' selector skips ads that already have an id assigned."""
    ad_defaults = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "", "suffix": ""}}}).ad_defaults

    already_published = Ad.model_validate(base_ad_config | {
        "id": "12345",
        "title": "Already Published",
        "active": True,
    }).model_dump()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        dicts.save_dict(ad_dir / "published.yaml", already_published)

        config_file = temp_path / "config.yaml"
        config_file.write_text("")

        with patch("kleinanzeigen_bot.utils.dicts.load_dict", side_effect = [already_published, {}]):
            ads = load_ads(
                config_file_path = str(config_file),
                ad_file_patterns = ["ads/*.yaml"],
                ad_defaults = ad_defaults,
                categories = {},
                ads_selector = "new",
                command = "publish",
            )
            assert len(ads) == 0


def test_load_ads_with_numeric_ids_includes_only_specified(
    base_ad_config:dict[str, Any], test_bot_config:Config
) -> None:
    """Numeric ID selector loads only ads with matching IDs."""
    ad_defaults = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "", "suffix": ""}}}).ad_defaults

    ad1 = Ad.model_validate(base_ad_config | {"id": 101, "title": "Ad Number 101", "active": True}).model_dump()
    ad2 = Ad.model_validate(base_ad_config | {"id": 202, "title": "Ad Number 202", "active": True}).model_dump()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        dicts.save_dict(ad_dir / "ad101.yaml", ad1)
        dicts.save_dict(ad_dir / "ad202.yaml", ad2)

        config_file = temp_path / "config.yaml"
        config_file.write_text("")

        with patch("kleinanzeigen_bot.utils.dicts.load_dict", side_effect = [ad1, ad2, {}]):
            ads = load_ads(
                config_file_path = str(config_file),
                ad_file_patterns = ["ads/*.yaml"],
                ad_defaults = ad_defaults,
                categories = {},
                ads_selector = "101",
                command = "publish",
            )
            assert len(ads) == 1
            assert ads[0][1].id == 101


def test_load_ads_skips_inactive_before_numeric_id(
    base_ad_config:dict[str, Any], test_bot_config:Config
) -> None:
    """Inactive ads are skipped even when their numeric ID matches the selector."""
    ad_defaults = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "", "suffix": ""}}}).ad_defaults

    inactive_ad = Ad.model_validate(base_ad_config | {
        "id": 101, "title": "Inactive Ad Title", "active": False,
    }).model_dump()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        dicts.save_dict(ad_dir / "inactive.yaml", inactive_ad)

        config_file = temp_path / "config.yaml"
        config_file.write_text("")

        with patch("kleinanzeigen_bot.utils.dicts.load_dict", side_effect = [inactive_ad, {}]):
            ads = load_ads(
                config_file_path = str(config_file),
                ad_file_patterns = ["ads/*.yaml"],
                ad_defaults = ad_defaults,
                categories = {},
                ads_selector = "101",
                command = "publish",
            )
            # Inactive check happens before numeric ID filtering
            assert len(ads) == 0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _build_ad(
    base_ad_config:dict[str, Any],
    ad_id:int | None,
    title:str,
) -> tuple[str, Ad, dict[str, Any]]:
    """Build an (ad_file, Ad, raw_dict) tuple for use in update_content_hashes tests."""
    ad_file = f"/fake/path/{title.replace(' ', '_')}.yaml"
    ad_cfg = Ad.model_validate(base_ad_config | {"id": str(ad_id) if ad_id else None, "title": title})
    ad_cfg_orig:dict[str, Any] = ad_cfg.model_dump()
    return ad_file, ad_cfg, ad_cfg_orig
