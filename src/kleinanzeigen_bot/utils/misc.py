"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import asyncio, decimal, re, sys, time
from collections.abc import Callable
from datetime import datetime, timedelta
from gettext import gettext as _
from typing import Any, TypeVar

from . import i18n

# https://mypy.readthedocs.io/en/stable/generics.html#generic-functions
T = TypeVar('T')


def ensure(condition:Any | bool | Callable[[], bool], error_message:str, timeout:float = 5, poll_requency:float = 0.5) -> None:
    """
    :param timeout: timespan in seconds until when the condition must become `True`, default is 5 seconds
    :param poll_requency: sleep interval between calls in seconds, default is 0.5 seconds
    :raises AssertionError: if condition did not come `True` within given timespan
    """
    if not isinstance(condition, Callable):  # type: ignore[arg-type] # https://github.com/python/mypy/issues/6864
        if condition:
            return
        raise AssertionError(_(error_message))

    if timeout < 0:
        raise AssertionError("[timeout] must be >= 0")
    if poll_requency < 0:
        raise AssertionError("[poll_requency] must be >= 0")

    start_at = time.time()
    while not condition():  # type: ignore[operator]
        elapsed = time.time() - start_at
        if elapsed >= timeout:
            raise AssertionError(_(error_message))
        time.sleep(poll_requency)


def is_frozen() -> bool:
    """
    >>> is_frozen()
    False
    """
    return getattr(sys, "frozen", False)


async def ainput(prompt: str) -> str:
    return await asyncio.to_thread(input, f'{prompt} ')


def parse_decimal(number:float | int | str) -> decimal.Decimal:
    """
    >>> parse_decimal(5)
    Decimal('5')

    >>> parse_decimal(5.5)
    Decimal('5.5')

    >>> parse_decimal("5.5")
    Decimal('5.5')

    >>> parse_decimal("5,5")
    Decimal('5.5')

    >>> parse_decimal("1.005,5")
    Decimal('1005.5')

    >>> parse_decimal("1,005.5")
    Decimal('1005.5')
    """
    try:
        return decimal.Decimal(number)
    except decimal.InvalidOperation as ex:
        parts = re.split("[.,]", str(number))
        try:
            return decimal.Decimal("".join(parts[:-1]) + "." + parts[-1])
        except decimal.InvalidOperation:
            raise decimal.DecimalException(f"Invalid number format: {number}") from ex


def parse_datetime(date:datetime | str | None) -> datetime | None:
    """
    >>> parse_datetime(datetime(2020, 1, 1, 0, 0))
    datetime.datetime(2020, 1, 1, 0, 0)

    >>> parse_datetime("2020-01-01T00:00:00")
    datetime.datetime(2020, 1, 1, 0, 0)

    >>> parse_datetime(None)

    """
    if date is None:
        return None
    if isinstance(date, datetime):
        return date
    return datetime.fromisoformat(date)


def parse_duration(text:str) -> timedelta:
    """
    Parses a human-readable duration string into a datetime.timedelta.

    Supported units:
      - d: days
      - h: hours
      - m: minutes
      - s: seconds

    Examples:
    >>> parse_duration("1h 30m")
    datetime.timedelta(seconds=5400)

    >>> parse_duration("2d 4h 15m 10s")
    datetime.timedelta(days=2, seconds=15310)

    >>> parse_duration("45m")
    datetime.timedelta(seconds=2700)

    >>> parse_duration("3d")
    datetime.timedelta(days=3)

    >>> parse_duration("5h 5h")
    datetime.timedelta(seconds=36000)

    >>> parse_duration("invalid input")
    datetime.timedelta(0)
    """
    pattern = re.compile(r'(\d+)\s*([dhms])')
    parts = pattern.findall(text.lower())
    kwargs: dict[str, int] = {}
    for value, unit in parts:
        if unit == 'd':
            kwargs['days'] = kwargs.get('days', 0) + int(value)
        elif unit == 'h':
            kwargs['hours'] = kwargs.get('hours', 0) + int(value)
        elif unit == 'm':
            kwargs['minutes'] = kwargs.get('minutes', 0) + int(value)
        elif unit == 's':
            kwargs['seconds'] = kwargs.get('seconds', 0) + int(value)
    return timedelta(**kwargs)


def format_timedelta(td: timedelta) -> str:
    """
    Formats a timedelta into a human-readable string using the pluralize utility.

    >>> format_timedelta(timedelta(seconds=90))
    '1 minute, 30 seconds'
    >>> format_timedelta(timedelta(hours=1))
    '1 hour'
    >>> format_timedelta(timedelta(days=2, hours=5))
    '2 days, 5 hours'
    >>> format_timedelta(timedelta(0))
    '0 seconds'
    """
    days = td.days
    seconds = td.seconds
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []

    if days:
        parts.append(i18n.pluralize("day", days))
    if hours:
        parts.append(i18n.pluralize("hour", hours))
    if minutes:
        parts.append(i18n.pluralize("minute", minutes))
    if seconds:
        parts.append(i18n.pluralize("second", seconds))

    return ", ".join(parts) if parts else i18n.pluralize("second", 0)
