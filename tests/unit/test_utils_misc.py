# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio
import decimal
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sanitize_filename import sanitize

from kleinanzeigen_bot.utils import misc
from kleinanzeigen_bot.utils.misc import sanitize_folder_name


def test_now_returns_utc_datetime() -> None:
    dt = misc.now()
    assert dt.tzinfo is not None
    assert dt.tzinfo.utcoffset(dt) == timedelta(0)


def test_is_frozen_default() -> None:
    assert misc.is_frozen() is False


def test_is_frozen_true(monkeypatch:pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising = False)
    assert misc.is_frozen() is True


def test_ainput_is_coroutine() -> None:
    assert asyncio.iscoroutinefunction(misc.ainput)


def test_parse_decimal_valid_inputs() -> None:
    assert misc.parse_decimal(5) == decimal.Decimal("5")
    assert misc.parse_decimal(5.5) == decimal.Decimal("5.5")
    assert misc.parse_decimal("5.5") == decimal.Decimal("5.5")
    assert misc.parse_decimal("5,5") == decimal.Decimal("5.5")
    assert misc.parse_decimal("1.005,5") == decimal.Decimal("1005.5")
    assert misc.parse_decimal("1,005.5") == decimal.Decimal("1005.5")


def test_parse_decimal_invalid_input() -> None:
    with pytest.raises(decimal.DecimalException):
        misc.parse_decimal("not_a_number")


def test_parse_datetime_none_returns_none() -> None:
    assert misc.parse_datetime(None) is None


def test_parse_datetime_from_datetime() -> None:
    dt = datetime(2020, 1, 1, 0, 0, tzinfo = timezone.utc)
    assert misc.parse_datetime(dt, add_timezone_if_missing = False) == dt


def test_parse_datetime_from_string() -> None:
    dt_str = "2020-01-01T00:00:00"
    result = misc.parse_datetime(dt_str, add_timezone_if_missing = False)
    assert result == datetime(2020, 1, 1, 0, 0)  # noqa: DTZ001


def test_parse_duration_various_inputs() -> None:
    assert misc.parse_duration("1h 30m") == timedelta(hours = 1, minutes = 30)
    assert misc.parse_duration("2d 4h 15m 10s") == timedelta(days = 2, hours = 4, minutes = 15, seconds = 10)
    assert misc.parse_duration("45m") == timedelta(minutes = 45)
    assert misc.parse_duration("3d") == timedelta(days = 3)
    assert misc.parse_duration("5h 5h") == timedelta(hours = 10)
    assert misc.parse_duration("invalid input") == timedelta(0)


def test_format_timedelta_examples() -> None:
    assert misc.format_timedelta(timedelta(seconds = 90)) == "1 minute, 30 seconds"
    assert misc.format_timedelta(timedelta(hours = 1)) == "1 hour"
    assert misc.format_timedelta(timedelta(days = 2, hours = 5)) == "2 days, 5 hours"
    assert misc.format_timedelta(timedelta(0)) == "0 seconds"


class Dummy:
    def __init__(self, contact:object) -> None:
        self.contact = contact


def test_get_attr_object_and_dict() -> None:
    assert misc.get_attr(Dummy({"email": "user@example.com"}), "contact.email") == "user@example.com"
    assert misc.get_attr(Dummy({"email": "user@example.com"}), "contact.foo") is None
    assert misc.get_attr(Dummy({"email": None}), "contact.email", default = "n/a") == "n/a"
    assert misc.get_attr(Dummy(None), "contact.email", default = "n/a") == "n/a"
    assert misc.get_attr({"contact": {"email": "data@example.com"}}, "contact.email") == "data@example.com"
    assert misc.get_attr({"contact": {"email": "user@example.com"}}, "contact.foo") is None
    assert misc.get_attr({"contact": {"email": None}}, "contact.email", default = "n/a") == "n/a"
    assert misc.get_attr({}, "contact.email", default = "none") == "none"


def test_ensure_negative_timeout() -> None:
    with pytest.raises(AssertionError, match = r"\[timeout\] must be >= 0"):
        misc.ensure(lambda: True, "Should fail", timeout = -1)


def test_ensure_negative_poll_frequency() -> None:
    with pytest.raises(AssertionError, match = r"\[poll_frequency\] must be >= 0"):
        misc.ensure(lambda: True, "Should fail", poll_frequency = -1)


def test_ensure_callable_condition_becomes_true(monkeypatch:pytest.MonkeyPatch) -> None:
    # Should return before timeout if condition becomes True
    state = {"called": 0}

    def cond() -> bool:
        state["called"] += 1
        return state["called"] > 2
    misc.ensure(cond, "Should not fail", timeout = 1, poll_frequency = 0.01)


def test_ensure_callable_condition_timeout() -> None:
    # Should raise AssertionError after timeout if condition never True
    with pytest.raises(AssertionError):
        misc.ensure(lambda: False, "Timeout fail", timeout = 0.05, poll_frequency = 0.01)


def test_ensure_non_callable_truthy_and_falsy() -> None:
    # Truthy values should not raise
    misc.ensure(True, "Should not fail for True")
    misc.ensure("Some Value", "Should not fail for non-empty string")
    misc.ensure(123, "Should not fail for positive int")
    misc.ensure(-123, "Should not fail for negative int")

    # Falsy values should raise AssertionError
    with pytest.raises(AssertionError):
        misc.ensure(False, "Should fail for False")
    with pytest.raises(AssertionError):
        misc.ensure(0, "Should fail for 0")
    with pytest.raises(AssertionError):
        misc.ensure("", "Should fail for empty string")
    with pytest.raises(AssertionError):
        misc.ensure(None, "Should fail for None")


# --- Test sanitize_folder_name function ---
@pytest.mark.parametrize(
    ("test_input", "expected_output", "description"),
    [
        # Basic sanitization
        ("My Ad Title!", "My Ad Title!", "Basic sanitization"),

        # Unicode normalization (sanitize-filename changes normalization)
        ("café", "cafe\u0301", "Unicode normalization"),
        ("caf\u00e9", "cafe\u0301", "Unicode normalization from escaped"),

        # Edge cases
        ("", "untitled", "Empty string"),
        ("   ", "untitled", "Whitespace only"),
        ("___", "___", "Multiple underscores (not collapsed)"),

        # Control characters (removed by sanitize-filename)
        ("Ad\x00with\x1fcontrol", "Adwithcontrol", "Control characters removed"),

        # Multiple consecutive underscores (sanitize-filename doesn't collapse them)
        ("Ad___with___multiple___underscores", "Ad___with___multiple___underscores", "Multiple underscores preserved"),

        # Special characters (removed by sanitize-filename)
        ('file<with>invalid:chars"|?*', "filewithinvalidchars", "Special characters removed"),
        ("file\\with\\backslashes", "filewithbackslashes", "Backslashes removed"),
        ("file/with/slashes", "filewithslashes", "Forward slashes removed"),

        # Path traversal attempts (handled by sanitize-filename)
        ("Title with ../../etc/passwd", "Title with ....etcpasswd", "Path traversal attempt"),
        ("Title with C:\\Windows\\System32\\cmd.exe", "Title with CWindowsSystem32cmd.exe", "Windows path traversal"),

        # XSS attempts (handled by sanitize-filename)
        ('Title with <script>alert("xss")</script>', "Title with scriptalert(xss)script", "XSS attempt"),
    ],
)
def test_sanitize_folder_name_basic(test_input:str, expected_output:str, description:str) -> None:
    """Test sanitize_folder_name function with various inputs."""
    result = sanitize_folder_name(test_input)
    assert result == expected_output, f"Failed for '{test_input}': {description}"


@pytest.mark.parametrize(
    ("test_input", "max_length", "expected_output", "description"),
    [
        # Length truncation
        ("Very long advertisement title that exceeds the maximum length and should be truncated", 50,
         "Very long advertisement title that exceeds the", "Length truncation"),

        # Word boundary truncation
        ("Short words but very long title", 20, "Short words but", "Word boundary truncation"),

        # Edge case: no word boundary found
        ("VeryLongWordWithoutSpaces", 10, "VeryLongWo", "No word boundary truncation"),

        # Test default max_length (100)
        ("This is a reasonable advertisement title that fits within the default limit", 100,
         "This is a reasonable advertisement title that fits within the default limit", "Default max_length"),
    ],
)
def test_sanitize_folder_name_truncation(test_input:str, max_length:int, expected_output:str, description:str) -> None:
    """Test sanitize_folder_name function with length truncation."""
    result = sanitize_folder_name(test_input, max_length = max_length)
    assert len(result) <= max_length, f"Result exceeds max_length for '{test_input}': {description}"
    assert result == expected_output, f"Failed for '{test_input}' with max_length={max_length}: {description}"


# --- Test sanitize-filename behavior directly (since it's consistent across platforms) ---
@pytest.mark.parametrize(
    ("test_input", "expected_output"),
    [
        # Test sanitize-filename behavior (consistent across platforms)
        ("test/file", "testfile"),
        ("test\\file", "testfile"),
        ("test<file", "testfile"),
        ("test>file", "testfile"),
        ('test"file', "testfile"),
        ("test|file", "testfile"),
        ("test?file", "testfile"),
        ("test*file", "testfile"),
        ("test:file", "testfile"),
        ("CON", "__CON"),
        ("PRN", "__PRN"),
        ("AUX", "__AUX"),
        ("NUL", "__NUL"),
        ("COM1", "__COM1"),
        ("LPT1", "__LPT1"),
        ("file/with/slashes", "filewithslashes"),
        ("file\\with\\backslashes", "filewithbackslashes"),
        ('file<with>invalid:chars"|?*', "filewithinvalidchars"),
        ("file\x00with\x1fcontrol", "filewithcontrol"),
        ("file___with___underscores", "file___with___underscores"),
    ],
)
def test_sanitize_filename_behavior(test_input:str, expected_output:str) -> None:
    """Test sanitize-filename behavior directly (consistent across platforms)."""
    result = sanitize(test_input)
    assert result == expected_output, f"sanitize-filename behavior mismatch for '{test_input}'"


# --- Test sanitize_folder_name cross-platform consistency ---
@pytest.mark.parametrize(
    "test_input",
    [
        "normal_filename",
        "filename with spaces",
        "filename_with_underscores",
        "filename-with-dashes",
        "filename.with.dots",
        "filename123",
        "café_filename",
        "filename\x00with\x1fcontrol",  # Control characters
    ],
)
def test_sanitize_folder_name_cross_platform_consistency(
    monkeypatch:pytest.MonkeyPatch,
    test_input:str
) -> None:
    """Test that sanitize_folder_name produces consistent results across platforms for safe inputs."""
    platforms = ["Windows", "Darwin", "Linux"]
    results = []

    for platform in platforms:
        monkeypatch.setattr("sys.platform", platform.lower())
        result = sanitize_folder_name(test_input)
        results.append(result)

    # All platforms should produce the same result for safe inputs
    assert len(set(results)) == 1, f"Cross-platform inconsistency for '{test_input}': {results}"
