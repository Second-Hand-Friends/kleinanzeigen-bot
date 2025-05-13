# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import ctypes, gettext, inspect, locale, logging, os, sys  # isort: skip
from collections.abc import Sized
from typing import Any, Final, NamedTuple

from kleinanzeigen_bot import resources

from . import dicts, reflect

__all__ = [
    "Locale",
    "get_current_locale",
    "pluralize",
    "set_current_locale",
    "translate"
]

LOG:Final[logging.Logger] = logging.getLogger(__name__)


class Locale(NamedTuple):

    language:str  # Language code (e.g., "en", "de")
    region:str | None = None  # Region code (e.g., "US", "DE")
    encoding:str = "UTF-8"  # Encoding format (e.g., "UTF-8")

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
    def of(locale_string:str) -> "Locale":
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
    """
    Detects the system language, returning a tuple of (language, region, encoding).
    - On macOS/Linux, it uses the LANG environment variable.
    - On Windows, it uses the Windows API via ctypes to get the default UI language.

    Returns:
        (language, region, encoding): e.g. ("en", "US", "UTF-8")
    """
    lang = os.environ.get("LANG", None)

    if not lang and os.name == "nt":  # Windows
        try:
            lang = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "en_US")  # type: ignore[attr-defined,unused-ignore] # mypy
        except Exception:
            LOG.warning("Error detecting language on Windows", exc_info = True)

    return Locale.of(lang) if lang else Locale("en", "US", "UTF-8")


_CURRENT_LOCALE:Locale = _detect_locale()
_TRANSLATIONS:dict[str, Any] | None = None


def translate(text:object, caller:inspect.FrameInfo | None) -> str:
    text = str(text)
    if not caller:
        return text

    global _TRANSLATIONS  # noqa: PLW0603 Using the global statement to update `...` is discouraged
    if _TRANSLATIONS is None:
        try:
            _TRANSLATIONS = dicts.load_dict_from_module(resources, f"translations.{_CURRENT_LOCALE[0]}.yaml")
        except FileNotFoundError:
            _TRANSLATIONS = {}

    if not _TRANSLATIONS:
        return text

    module_name = caller.frame.f_globals.get("__name__")  # pylint: disable=redefined-outer-name
    file_basename = os.path.splitext(os.path.basename(caller.filename))[0]
    if module_name and module_name.endswith(f".{file_basename}"):
        module_name = module_name[:-(len(file_basename) + 1)]
    if module_name:
        module_name = module_name.replace(".", "/")
    file_key = f"{file_basename}.py" if module_name == file_basename else f"{module_name}/{file_basename}.py"
    translation = dicts.safe_get(_TRANSLATIONS,
        file_key,
        caller.function,
        text
    )
    return translation if translation else text


# replace gettext.gettext with custom _translate function
_original_gettext = gettext.gettext
gettext.gettext = lambda message: translate(_original_gettext(message), reflect.get_caller())
for module_name, module in sys.modules.copy().items():
    if module is None or module_name in sys.builtin_module_names:
        continue
    if hasattr(module, "_") and module._ is _original_gettext:
        module._ = gettext.gettext  # type: ignore[attr-defined]
    if hasattr(module, "gettext") and module.gettext is _original_gettext:
        module.gettext = gettext.gettext  # type: ignore[attr-defined]


def get_current_locale() -> Locale:
    return _CURRENT_LOCALE


def set_current_locale(new_locale:Locale) -> None:
    global _CURRENT_LOCALE, _TRANSLATIONS  # noqa: PLW0603 Using the global statement to update `...` is discouraged
    if new_locale.language != _CURRENT_LOCALE.language:
        _TRANSLATIONS = None
    _CURRENT_LOCALE = new_locale


def pluralize(noun:str, count:int | Sized, *, prefix_with_count:bool = True) -> str:
    """
    >>> set_current_locale(Locale("en"))  # Setup for doctests
    >>> pluralize("field", 1)
    '1 field'
    >>> pluralize("field", 2)
    '2 fields'
    >>> pluralize("field", 2, prefix_with_count = False)
    'fields'
    """
    noun = translate(noun, reflect.get_caller())

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
    if len(noun) < 2:  # noqa: PLR2004 Magic value used in comparison
        return f"{prefix}{noun}s"
    if noun.endswith(("s", "sh", "ch", "x", "z")):
        return f"{prefix}{noun}es"
    if noun.endswith("y") and noun[-2].lower() not in "aeiou":
        return f"{prefix}{noun[:-1]}ies"
    return f"{prefix}{noun}s"
