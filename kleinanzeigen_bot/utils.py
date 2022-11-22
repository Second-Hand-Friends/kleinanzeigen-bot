"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import copy, decimal, json, logging, os, re, secrets, sys, traceback, time
from importlib.resources import read_text as get_resource_as_string
from collections.abc import Callable, Sized
from datetime import datetime
from types import FrameType, ModuleType, TracebackType
from typing import Any, Final, TypeVar

import coloredlogs, inflect
from ruamel.yaml import YAML

LOG_ROOT:Final[logging.Logger] = logging.getLogger()
LOG:Final[logging.Logger] = logging.getLogger("kleinanzeigen_bot.utils")

# https://mypy.readthedocs.io/en/stable/generics.html#generic-functions
T = TypeVar('T')


def abspath(relative_path:str, relative_to:str | None = None) -> str:
    """
    Makes a given relative path absolute based on another file/folder
    """
    if os.path.isabs(relative_path):
        return relative_path

    if not relative_to:
        return os.path.abspath(relative_path)

    if os.path.isfile(relative_to):
        relative_to = os.path.dirname(relative_to)

    return os.path.normpath(os.path.join(relative_to, relative_path))


def ensure(condition:Any | bool | Callable[[], bool], error_message:str, timeout:float = 5, poll_requency:float = 0.5) -> None:
    """
    :param timeout: timespan in seconds until when the condition must become `True`, default is 5 seconds
    :param poll_requency: sleep interval between calls in seconds, default is 0.5 seconds
    :raises AssertionError: if condition did not come `True` within given timespan
    """
    if not isinstance(condition, Callable):  # type: ignore[arg-type] # https://github.com/python/mypy/issues/6864
        if condition:
            return
        raise AssertionError(error_message)

    if timeout < 0:
        raise AssertionError("[timeout] must be >= 0")
    if poll_requency < 0:
        raise AssertionError("[poll_requency] must be >= 0")

    start_at = time.time()
    while not condition():  # type: ignore[operator]
        elapsed = time.time() - start_at
        if elapsed >= timeout:
            raise AssertionError(error_message)
        time.sleep(poll_requency)


def is_frozen() -> bool:
    """
    >>> is_frozen()
    False
    """
    return getattr(sys, "frozen", False)


def apply_defaults(
    target:dict[Any, Any],
    defaults:dict[Any, Any],
    ignore:Callable[[Any, Any], bool] = lambda _k, _v: False,
    override:Callable[[Any, Any], bool] = lambda _k, _v: False
) -> dict[Any, Any]:
    """
    >>> apply_defaults({}, {"foo": "bar"})
    {'foo': 'bar'}
    >>> apply_defaults({"foo": "foo"}, {"foo": "bar"})
    {'foo': 'foo'}
    >>> apply_defaults({"foo": ""}, {"foo": "bar"})
    {'foo': ''}
    >>> apply_defaults({}, {"foo": "bar"}, ignore = lambda k, _: k == "foo")
    {}
    >>> apply_defaults({"foo": ""}, {"foo": "bar"}, override = lambda _, v: v == "")
    {'foo': 'bar'}
    >>> apply_defaults({"foo": None}, {"foo": "bar"}, override = lambda _, v: v == "")
    {'foo': None}
    """
    for key, default_value in defaults.items():
        if key in target:
            if isinstance(target[key], dict) and isinstance(default_value, dict):
                apply_defaults(target[key], default_value, ignore = ignore)
            elif override(key, target[key]):
                target[key] = copy.deepcopy(default_value)
        elif not ignore(key, default_value):
            target[key] = copy.deepcopy(default_value)
    return target


def safe_get(a_map:dict[Any, Any], *keys:str) -> Any:
    """
    >>> safe_get({"foo": {}}, "foo", "bar") is None
    True
    >>> safe_get({"foo": {"bar": "some_value"}}, "foo", "bar")
    'some_value'
    """
    if a_map:
        for key in keys:
            try:
                a_map = a_map[key]
            except (KeyError, TypeError):
                return None
    return a_map


def configure_console_logging() -> None:
    stdout_log = logging.StreamHandler(sys.stderr)
    stdout_log.setLevel(logging.DEBUG)
    stdout_log.setFormatter(coloredlogs.ColoredFormatter("[%(levelname)s] %(message)s"))
    stdout_log.addFilter(type("", (logging.Filter,), {
        "filter": lambda rec: rec.levelno <= logging.INFO
    }))
    LOG_ROOT.addHandler(stdout_log)

    stderr_log = logging.StreamHandler(sys.stderr)
    stderr_log.setLevel(logging.WARNING)
    stderr_log.setFormatter(coloredlogs.ColoredFormatter("[%(levelname)s] %(message)s"))
    LOG_ROOT.addHandler(stderr_log)


def on_exception(ex_type:type[BaseException], ex_value:Any, ex_traceback:TracebackType | None) -> None:
    if issubclass(ex_type, KeyboardInterrupt):
        sys.__excepthook__(ex_type, ex_value, ex_traceback)
    elif LOG.isEnabledFor(logging.DEBUG) or isinstance(ex_value, (AttributeError, ImportError, NameError, TypeError)):
        LOG.error("".join(traceback.format_exception(ex_type, ex_value, ex_traceback)))
    elif isinstance(ex_value, AssertionError):
        LOG.error(ex_value)
    else:
        LOG.error("%s: %s", ex_type.__name__, ex_value)


def on_exit() -> None:
    for handler in LOG_ROOT.handlers:
        handler.flush()


def on_sigint(_sig:int, _frame:FrameType | None) -> None:
    LOG.warning("Aborted on user request.")
    sys.exit(0)


def pause(min_ms:int = 200, max_ms:int = 2000) -> None:
    if max_ms <= min_ms:
        duration = min_ms
    else:
        duration = secrets.randbelow(max_ms - min_ms) + min_ms
    LOG.log(logging.INFO if duration > 1500 else logging.DEBUG, " ... pausing for %d ms ...", duration)
    time.sleep(duration / 1000)


def pluralize(word:str, count:int | Sized, prefix:bool = True) -> str:
    """
    >>> pluralize("field", 1)
    '1 field'
    >>> pluralize("field", 2)
    '2 fields'
    >>> pluralize("field", 2, prefix = False)
    'fields'
    """
    if not hasattr(pluralize, "inflect"):
        pluralize.inflect = inflect.engine()
    if isinstance(count, Sized):
        count = len(count)
    plural:str = pluralize.inflect.plural_noun(word, count)
    if prefix:
        return f"{count} {plural}"
    return plural


def load_dict(filepath:str, content_label:str = "") -> dict[str, Any]:
    """
    :raises FileNotFoundError
    """
    data = load_dict_if_exists(filepath, content_label)
    if data is None:
        raise FileNotFoundError(filepath)
    return data


def load_dict_if_exists(filepath:str, content_label:str = "") -> dict[str, Any] | None:
    filepath = os.path.abspath(filepath)
    LOG.info("Loading %s[%s]...", content_label and content_label + " from " or "", filepath)

    _, file_ext = os.path.splitext(filepath)
    if file_ext not in [".json", ".yaml", ".yml"]:
        raise ValueError(f'Unsupported file type. The file name "{filepath}" must end with *.json, *.yaml, or *.yml')

    if not os.path.exists(filepath):
        return None

    with open(filepath, encoding = "utf-8") as file:
        return json.load(file) if filepath.endswith(".json") else YAML().load(file)


def load_dict_from_module(module:ModuleType, filename:str, content_label:str = "") -> dict[str, Any]:
    """
    :raises FileNotFoundError
    """
    LOG.debug("Loading %s[%s.%s]...", content_label and content_label + " from " or "", module.__name__, filename)

    _, file_ext = os.path.splitext(filename)
    if file_ext not in (".json", ".yaml", ".yml"):
        raise ValueError(f'Unsupported file type. The file name "{filename}" must end with *.json, *.yaml, or *.yml')

    content = get_resource_as_string(module, filename)
    return json.loads(content) if filename.endswith(".json") else YAML().load(content)


def save_dict(filepath:str, content:dict[str, Any]) -> None:
    filepath = os.path.abspath(filepath)
    LOG.info("Saving [%s]...", filepath)
    with open(filepath, "w", encoding = "utf-8") as file:
        if filepath.endswith(".json"):
            file.write(json.dumps(content, indent = 2, ensure_ascii = False))
        else:
            yaml = YAML()
            yaml.indent(mapping = 2, sequence = 4, offset = 2)
            yaml.allow_duplicate_keys = False
            yaml.explicit_start = False
            yaml.dump(content, file)


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


def extract_ad_id_from_ad_link(url: str) -> int:
    """
    Extracts the ID of an ad, given by its reference link.

    :param url: the URL to the ad page
    :return: the ad ID, a (ten-digit) integer number
    """
    num_part = url.split('/')[-1]  # suffix
    id_part = num_part.split('-')[0]

    try:
        return int(id_part)
    except ValueError:
        print('The ad ID could not be extracted from the given ad reference!')
        return -1
