"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import logging
import pytest
from typing import Any, Dict, Final

from kleinanzeigen_bot import utils
from kleinanzeigen_bot.i18n import get_translating_logger

utils.configure_console_logging()

LOG:Final[logging.Logger] = get_translating_logger("kleinanzeigen_bot")
LOG.setLevel(logging.DEBUG)

@pytest.fixture
def sample_config() -> Dict[str, Any]:
    return {
        "login": {
            "username": "test_user",
            "password": "test_password"
        },
        "browser": {
            "arguments": [],
            "binary_location": None,
            "extensions": [],
            "use_private_window": True,
            "user_data_dir": None,
            "profile_name": None
        },
        "ad_defaults": {
            "description": {
                "prefix": "",
                "suffix": ""
            }
        },
        "ad_files": ["ads/*.yaml"]
    }

@pytest.fixture
def sample_ad_config() -> Dict[str, Any]:
    return {
        "title": "Test Item",
        "description": "Test Description",
        "price": "100",
        "price_type": "FIXED",
        "shipping_type": "PICKUP",
        "active": True,
        "contact": {
            "name": "Test User",
            "zipcode": "12345"
        },
        "images": [],
        "id": None,
        "created_on": None,
        "updated_on": None,
        "republication_interval": 30
    }