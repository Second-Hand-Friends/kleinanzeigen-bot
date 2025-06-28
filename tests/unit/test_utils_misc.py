# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio
import decimal
import sys
from datetime import datetime, timedelta, timezone

import pytest

from kleinanzeigen_bot.utils import misc


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
