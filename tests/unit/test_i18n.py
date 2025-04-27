# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import pytest
from _pytest.monkeypatch import MonkeyPatch  # pylint: disable=import-private-name

from kleinanzeigen_bot.utils import i18n


@pytest.mark.parametrize(("lang", "expected"), [
    (None, ("en", "US", "UTF-8")),  # Test with no LANG variable (should default to ("en", "US", "UTF-8"))
    ("fr", ("fr", None, "UTF-8")),  # Test with just a language code
    ("fr_CA", ("fr", "CA", "UTF-8")),  # Test with language + region, no encoding
    ("pt_BR.iso8859-1", ("pt", "BR", "ISO8859-1")),  # Test with language + region + encoding
])
def test_detect_locale(monkeypatch:MonkeyPatch, lang:str | None, expected:i18n.Locale) -> None:
    """
    Pytest test case to verify detect_system_language() behavior under various LANG values.
    """
    # Clear or set the LANG environment variable as needed.
    if lang is None:
        monkeypatch.delenv("LANG", raising = False)
    else:
        monkeypatch.setenv("LANG", lang)

    # Call the function and compare the result to the expected output.
    result = i18n._detect_locale()  # pylint: disable=protected-access
    assert result == expected, f"For LANG={lang}, expected {expected} but got {result}"


@pytest.mark.parametrize(("lang", "noun", "count", "prefix_with_count", "expected"), [
    ("en", "field", 1, True, "1 field"),
    ("en", "field", 2, True, "2 fields"),
    ("en", "field", 2, False, "fields"),
    ("en", "attribute", 2, False, "attributes"),
    ("en", "bus", 2, False, "buses"),
    ("en", "city", 2, False, "cities"),
    ("de", "Feld", 1, True, "1 Feld"),
    ("de", "Feld", 2, True, "2 Felder"),
    ("de", "Feld", 2, False, "Felder"),
    ("de", "Anzeige", 2, False, "Anzeigen"),
    ("de", "Attribute", 2, False, "Attribute"),
    ("de", "Bild", 2, False, "Bilder"),
    ("de", "Datei", 2, False, "Dateien"),
    ("de", "Kategorie", 2, False, "Kategorien")
])
def test_pluralize(
    lang:str,
    noun:str,
    count:int,
    prefix_with_count:bool,
    expected:str
) -> None:
    i18n.set_current_locale(i18n.Locale(lang, "US", "UTF_8"))

    result = i18n.pluralize(noun, count, prefix_with_count = prefix_with_count)
    assert result == expected, f"For LANG={lang}, expected {expected} but got {result}"
