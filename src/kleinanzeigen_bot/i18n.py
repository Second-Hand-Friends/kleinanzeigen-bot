"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import ctypes, locale, logging, os
from collections.abc import Sized
from typing import Any, Final, NamedTuple

from .logging import get_logger

__all__ = [
    "Locale",
    "get_translating_logger",
]

LOG: Final[logging.Logger] = get_logger(__name__)


def _get_windows_ui_language() -> str | None:
    """Get the Windows UI language code. Only works on Windows."""
    try:
        # We need to access windll only on Windows, so we do the import here
        if not hasattr(ctypes, "windll"):  # Not on Windows
            return None
        # Access Windows-specific attributes through getattr to satisfy type checker
        windll: Any = getattr(ctypes, "windll")
        lang_code = windll.kernel32.GetUserDefaultUILanguage()
        return locale.windows_locale.get(lang_code, "en_US")
    except Exception:
        LOG.warning("Error detecting language on Windows", exc_info=True)
        return None


class Locale(NamedTuple):
    language: str  # Language code (e.g., "en", "de")
    region: str | None = None  # Region code (e.g., "US", "DE")
    encoding: str = "UTF-8"  # Encoding format (e.g., "UTF-8")

    def __str__(self) -> str:
        """
        >>> str(Locale("en", "US", "UTF-8"))
        'en_US.UTF-8'
        >>> str(Locale("en", "US"))
        'en_US.UTF-8'
        >>> str(Locale("en"))
        'en.UTF-8'
        >>> str(Locale("de", None, "UTF-8"))
        'de.UTF-8'
        """
        region_part = f"_{self.region}" if self.region else ""
        encoding_part = f".{self.encoding}" if self.encoding else ""
        return f"{self.language}{region_part}{encoding_part}"

    @staticmethod
    def of(locale_string: str) -> 'Locale':
        """
        >>> Locale.of("en_US.UTF-8")
        Locale(language='en', region='US', encoding='UTF-8')
        >>> Locale.of("de.UTF-8")
        Locale(language='de', region=None, encoding='UTF-8')
        >>> Locale.of("de_DE")
        Locale(language='de', region='DE', encoding='UTF-8')
        >>> Locale.of("en")
        Locale(language='en', region=None, encoding='UTF-8')
        >>> Locale.of("en.UTF-8")
        Locale(language='en', region=None, encoding='UTF-8')
        """
        parts = locale_string.split(".")
        language_and_region = parts[0]
        encoding = parts[1].upper() if len(parts) > 1 else "UTF-8"

        parts = language_and_region.split("_")
        language = parts[0]
        region = parts[1].upper() if len(parts) > 1 else None

        return Locale(language = language, region = region, encoding = encoding)


def _detect_locale() -> Locale:
    """Detects the system language."""
    lang = os.environ.get("LANG", None)
    if not lang and os.name == "nt":  # Windows
        lang = _get_windows_ui_language()
    return Locale.of(lang) if lang else Locale("en", "US", "UTF-8")


_CURRENT_LOCALE: Locale = _detect_locale()


def get_translating_logger(name: str | None = None) -> logging.Logger:
    """Returns a logger that translates messages before logging them."""
    return get_logger(name)


def get_current_locale() -> Locale:
    return _CURRENT_LOCALE


def set_current_locale(new_locale: Locale) -> None:
    global _CURRENT_LOCALE
    _CURRENT_LOCALE = new_locale


def pluralize(noun:str, count:int | Sized, prefix_with_count:bool = True) -> str:
    """
    >>> set_current_locale(Locale("en"))  # Setup for doctests
    >>> pluralize("field", 1)
    '1 field'
    >>> pluralize("field", 2)
    '2 fields'
    >>> pluralize("field", 2, prefix_with_count = False)
    'fields'
    """
    if isinstance(count, Sized):
        count = len(count)

    prefix = f"{count} " if prefix_with_count else ""

    if count == 1:
        return f"{prefix}{noun}"

    # German
    if _CURRENT_LOCALE.language == "de":
        # Special cases
        irregular_plurals = {
            "Attribute": "Attribute",
            "Bild": "Bilder",
            "Feld": "Felder",
        }
        if noun in irregular_plurals:
            return f"{prefix}{irregular_plurals[noun]}"
        for singular_suffix, plural_suffix in irregular_plurals.items():
            if noun.lower().endswith(singular_suffix):
                pluralized = noun[:-len(singular_suffix)] + plural_suffix.lower()
                return f"{prefix}{pluralized}"

        # Very simplified German rules
        if noun.endswith("ei"):
            return f"{prefix}{noun}en"  # Datei -> Dateien
        if noun.endswith("e"):
            return f"{prefix}{noun}n"  # Blume -> Blumen
        if noun.endswith(("el", "er", "en")):
            return f"{prefix}{noun}"  # Keller -> Keller
        if noun[-1] in "aeiou":
            return f"{prefix}{noun}s"  # Auto -> Autos
        return f"{prefix}{noun}e"  # Hund -> Hunde

    # English
    if len(noun) < 2:
        return f"{prefix}{noun}s"
    if noun.endswith(('s', 'sh', 'ch', 'x', 'z')):
        return f"{prefix}{noun}es"
    if noun.endswith('y') and noun[-2].lower() not in "aeiou":
        return f"{prefix}{noun[:-1]}ies"
    return f"{prefix}{noun}s"
