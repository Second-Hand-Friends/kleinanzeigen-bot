# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio, decimal, re, sys, time  # isort: skip
import unicodedata
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from gettext import gettext as _
from typing import Any, Mapping, TypeVar

from sanitize_filename import sanitize

from . import i18n

# https://mypy.readthedocs.io/en/stable/generics.html#generic-functions
T = TypeVar("T")


def ensure(
        condition:Any | bool | Callable[[], bool],  # noqa: FBT001 Boolean-typed positional argument in function definition
        error_message:str,
        timeout:float = 5,
        poll_frequency:float = 0.5
    ) -> None:
    """
    Ensure a condition is true, retrying until timeout.

    :param condition: The condition to check (bool, value, or callable returning bool)
    :param error_message: The error message to raise if the condition is not met
    :param timeout: maximum time to wait in seconds, default is 5 seconds
    :param poll_frequency: sleep interval between calls in seconds, default is 0.5 seconds
    :raises AssertionError: if the condition is not met within the timeout
    """
    if not isinstance(condition, Callable):  # type: ignore[arg-type] # https://github.com/python/mypy/issues/6864
        if condition:
            return
        raise AssertionError(_(error_message))

    if timeout < 0:
        raise AssertionError("[timeout] must be >= 0")
    if poll_frequency < 0:
        raise AssertionError("[poll_frequency] must be >= 0")

    start_at = time.time()
    while not condition():  # type: ignore[operator]
        elapsed = time.time() - start_at
        if elapsed >= timeout:
            raise AssertionError(_(error_message))
        time.sleep(poll_frequency)


def get_attr(obj:Mapping[str, Any] | Any, key:str, default:Any | None = None) -> Any:
    """
    Unified getter for attribute or key access on objects or dicts.
    Supports dot-separated paths for nested access.

    Args:
        obj: The object or dictionary to get the value from.
        key: The attribute or key name, possibly nested via dot notation (e.g. 'contact.email').
        default: A default value to return if the key/attribute path is not found.

    Returns:
        The found value or the default.

    Examples:
        >>> class User:
        ...     def __init__(self, contact): self.contact = contact

        # [object] normal nested access:
        >>> get_attr(User({'email': 'user@example.com'}), 'contact.email')
        'user@example.com'

        # [object] missing key at depth:
        >>> get_attr(User({'email': 'user@example.com'}), 'contact.foo') is None
        True

        # [object] explicit None treated as missing:
        >>> get_attr(User({'email': None}), 'contact.email', default='n/a')
        'n/a'

        # [object] parent in path is None:
        >>> get_attr(User(None), 'contact.email', default='n/a')
        'n/a'

        # [dict] normal nested access:
        >>> get_attr({'contact': {'email': 'data@example.com'}}, 'contact.email')
        'data@example.com'

        # [dict] missing key at depth:
        >>> get_attr({'contact': {'email': 'user@example.com'}}, 'contact.foo') is None
        True

        # [dict] explicit None treated as missing:
        >>> get_attr({'contact': {'email': None}}, 'contact.email', default='n/a')
        'n/a'

        # [dict] parent in path is None:
        >>> get_attr({}, 'contact.email', default='none')
        'none'
    """
    for part in key.split("."):
        obj = obj.get(part) if isinstance(obj, Mapping) else getattr(obj, part, None)
        if obj is None:
            return default

    return obj


def now() -> datetime:
    return datetime.now(timezone.utc)


def is_frozen() -> bool:
    """
    >>> is_frozen()
    False
    """
    return getattr(sys, "frozen", False)


async def ainput(prompt:str) -> str:
    return await asyncio.to_thread(input, f"{prompt} ")


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


def parse_datetime(
    date:datetime | str | None,
    *,
    add_timezone_if_missing:bool = True,
    use_local_timezone:bool = True
) -> datetime | None:
    """
    Parses a datetime object or ISO-formatted string.

    Args:
        date: The input datetime object or ISO string.
        add_timezone_if_missing: If True, add timezone info if missing.
        use_local_timezone: If True, use local timezone; otherwise UTC if adding timezone.

    Returns:
        A timezone-aware or naive datetime object, depending on parameters.

    >>> parse_datetime(datetime(2020, 1, 1, 0, 0), add_timezone_if_missing = False)
    datetime.datetime(2020, 1, 1, 0, 0)

    >>> parse_datetime("2020-01-01T00:00:00", add_timezone_if_missing = False)
    datetime.datetime(2020, 1, 1, 0, 0)

    >>> parse_datetime(None)

    """
    if date is None:
        return None

    dt = date if isinstance(date, datetime) else datetime.fromisoformat(date)

    if dt.tzinfo is None and add_timezone_if_missing:
        dt = (
            dt.astimezone() if use_local_timezone
            else dt.replace(tzinfo = timezone.utc)
        )

    return dt


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
    pattern = re.compile(r"(\d+)\s*([dhms])")
    parts = pattern.findall(text.lower())
    kwargs:dict[str, int] = {}
    for value, unit in parts:
        if unit == "d":
            kwargs["days"] = kwargs.get("days", 0) + int(value)
        elif unit == "h":
            kwargs["hours"] = kwargs.get("hours", 0) + int(value)
        elif unit == "m":
            kwargs["minutes"] = kwargs.get("minutes", 0) + int(value)
        elif unit == "s":
            kwargs["seconds"] = kwargs.get("seconds", 0) + int(value)
    return timedelta(**kwargs)


def format_timedelta(td:timedelta) -> str:
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


def sanitize_folder_name(name:str, max_length:int = 100) -> str:
    """
    Sanitize a string for use as a folder name using `sanitize-filename`.

    - Cross-platform safe (Windows/macOS/Linux)
    - Removes invalid characters and Windows reserved names
    - Handles path traversal attempts
    - Truncates to `max_length`

    Args:
        name: The input string.
        max_length: Maximum length of the resulting folder name (default: 100).

    Returns:
        A sanitized folder name (falls back to "untitled" when empty).
    """
    # Normalize whitespace and handle empty input
    raw = (name or "").strip()
    if not raw:
        return "untitled"

    raw = unicodedata.normalize("NFC", raw)
    safe:str = sanitize(raw)

    # Truncate with word-boundary preference
    if len(safe) > max_length:
        truncated = safe[:max_length]
        last_break = max(truncated.rfind(" "), truncated.rfind("_"))
        safe = truncated[:last_break] if last_break > int(max_length * 0.7) else truncated

    return safe
