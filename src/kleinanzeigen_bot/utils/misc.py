"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import asyncio, decimal, re, sys, time
from collections.abc import Callable
from datetime import datetime
from gettext import gettext as _
from typing import Any, TypeVar

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
