# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
Browser runtime configuration.

Contains BrowserConfig, the data class for browser launcher settings,
shared between WebScrapingMixin and browser diagnostics.
"""


class BrowserConfig:
    """Configuration for browser launcher settings.

    Attributes:
        arguments: Additional browser command-line arguments.
        binary_location: Path to the browser executable, or None for auto-detection.
        extensions: List of extension paths to load.
        use_private_window: Whether to start in incognito/private mode.
        user_data_dir: Path to browser user data directory.
        profile_name: Browser profile directory name.
    """

    def __init__(self) -> None:
        self.arguments:list[str] = []
        self.binary_location:str | None = None
        self.extensions:list[str] = []
        self.use_private_window:bool = True
        self.user_data_dir:str | None = None
        self.profile_name:str | None = None
