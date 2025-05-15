# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from kleinanzeigen_bot.model.ad_model import AdPartial


def test_update_content_hash() -> None:
    minimal_ad_cfg = {
        "id": "123456789",
        "title": "Test Ad Title",
        "category": "160",
        "description": "Test Description",
    }
    minimal_ad_cfg_hash = "ae3defaccd6b41f379eb8de17263caa1bd306e35e74b11aa03a4738621e96ece"

    assert AdPartial.model_validate(minimal_ad_cfg).update_content_hash().content_hash == minimal_ad_cfg_hash

    assert AdPartial.model_validate(minimal_ad_cfg | {
        "id": "123456789",
        "created_on": "2025-05-08T09:34:03",
        "updated_on": "2025-05-14T20:43:16",
        "content_hash": "5753ead7cf42b0ace5fe658ecb930b3a8f57ef49bd52b7ea2d64b91b2c75517e"
    }).update_content_hash().content_hash == minimal_ad_cfg_hash

    assert AdPartial.model_validate(minimal_ad_cfg | {
        "active": None,
        "images": None,
        "shipping_options": None,
        "special_attributes": None,
        "contact": None,
    }).update_content_hash().content_hash == minimal_ad_cfg_hash

    assert AdPartial.model_validate(minimal_ad_cfg | {
        "active": True,
        "images": [],
        "shipping_options": [],
        "special_attributes": {},
        "contact": {},
    }).update_content_hash().content_hash != minimal_ad_cfg_hash
