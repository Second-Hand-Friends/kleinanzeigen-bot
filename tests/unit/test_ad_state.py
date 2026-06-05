# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from pathlib import Path
from typing import Any

from kleinanzeigen_bot.ad_state import apply_after_delete_policy, relative_ad_path
from kleinanzeigen_bot.model.ad_model import Ad


def _base_ad_config() -> dict[str, Any]:
    return {
        "id": None,
        "title": "Test Title",
        "description": "Test Description",
        "type": "OFFER",
        "price_type": "FIXED",
        "price": 100,
        "shipping_type": "SHIPPING",
        "shipping_options": [],
        "category": "160",
        "special_attributes": {},
        "sell_directly": False,
        "images": [],
        "active": True,
        "republication_interval": 7,
        "created_on": None,
        "contact": {
            "name": "Test User",
            "zipcode": "12345",
            "location": "Test City",
            "street": "",
            "phone": "",
        },
    }


def _make_ad() -> tuple[Ad, dict[str, Any]]:
    ad_cfg = Ad.model_validate(_base_ad_config() | {
        "id": 12345,
        "active": True,
        "created_on": "2024-06-01T12:00:00",
        "updated_on": "2024-06-10T08:30:00",
        "content_hash": "abc123",
        "repost_count": 3,
        "price_reduction_count": 1,
    })
    return ad_cfg, ad_cfg.model_dump()


def test_relative_ad_path_returns_relative_path_when_within_config_dir(tmp_path:Path) -> None:
    config_file_path = tmp_path / "config.yaml"
    ad_file = tmp_path / "ads" / "ad.yaml"

    assert relative_ad_path(ad_file, config_file_path) == "ads/ad.yaml"


def test_relative_ad_path_preserves_path_when_outside_config_dir(tmp_path:Path) -> None:
    config_file_path = tmp_path / "config.yaml"
    ad_file = tmp_path.parent / "outside" / "ad.yaml"

    assert relative_ad_path(ad_file, config_file_path) == str(ad_file)


def test_apply_after_delete_policy_reset_clears_metadata_and_resets_model_state() -> None:
    ad_cfg, ad_cfg_orig = _make_ad()

    result = apply_after_delete_policy(ad_cfg, ad_cfg_orig, mode = "RESET")

    assert result is True
    assert ad_cfg.id is None
    assert ad_cfg.created_on is None
    assert ad_cfg.updated_on is None
    assert ad_cfg.content_hash is None
    assert ad_cfg.repost_count == 0
    assert ad_cfg.price_reduction_count == 0
    assert "id" not in ad_cfg_orig
    assert "created_on" not in ad_cfg_orig
    assert "updated_on" not in ad_cfg_orig
    assert "content_hash" not in ad_cfg_orig
    assert "repost_count" not in ad_cfg_orig
    assert "price_reduction_count" not in ad_cfg_orig


def test_apply_after_delete_policy_disable_updates_model_and_dict() -> None:
    ad_cfg, ad_cfg_orig = _make_ad()

    result = apply_after_delete_policy(ad_cfg, ad_cfg_orig, mode = "DISABLE")

    assert result is True
    assert ad_cfg.active is False
    assert ad_cfg_orig["active"] is False


def test_apply_after_delete_policy_none_is_non_mutating() -> None:
    ad_cfg, ad_cfg_orig = _make_ad()
    original_model_dump = ad_cfg.model_dump()
    original_dict = ad_cfg_orig.copy()

    result = apply_after_delete_policy(ad_cfg, ad_cfg_orig, mode = "NONE")

    assert result is False
    assert ad_cfg.model_dump() == original_model_dump
    assert ad_cfg_orig == original_dict
