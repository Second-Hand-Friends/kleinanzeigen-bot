"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import asyncio, copy, decimal, hashlib, inspect, json, logging, os, re, socket, sys, time
from collections.abc import Callable
from datetime import datetime
from gettext import gettext as _
from importlib.resources import read_text as get_resource_as_string
from types import FrameType, ModuleType, TracebackType
from typing import Any, Final, TypeVar

import colorama
from ruamel.yaml import YAML

from .logging import LOG_ROOT, get_logger

LOG: Final[logging.Logger] = get_logger(__name__)

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


def get_caller(depth: int = 1) -> inspect.FrameInfo | None:
    stack = inspect.stack()
    try:
        for frame in stack[depth + 1:]:
            if frame.function and frame.function != "<lambda>":
                return frame
        return None
    finally:
        del stack  # Clean up the stack to avoid reference cycles


def is_frozen() -> bool:
    """
    >>> is_frozen()
    False
    """
    return getattr(sys, "frozen", False)


def is_integer(obj:Any) -> bool:
    try:
        int(obj)
        return True
    except (ValueError, TypeError):
        return False


def is_port_open(host:str, port:int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(1)
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


async def ainput(prompt: str) -> str:
    return await asyncio.to_thread(input, f'{prompt} ')


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
    """Configure console logging with colors and formatting."""
    class CustomFormatter(logging.Formatter):
        LEVEL_COLORS = {
            logging.DEBUG: colorama.Fore.BLUE,
            logging.INFO: colorama.Fore.GREEN,
            logging.WARNING: colorama.Fore.YELLOW,
            logging.ERROR: colorama.Fore.RED,
            logging.CRITICAL: colorama.Fore.MAGENTA,
        }

        def format(self, record: logging.LogRecord) -> str:
            try:
                record = copy.deepcopy(record)
                level_color = self.LEVEL_COLORS.get(record.levelno, "")
                record.levelname = f"{level_color}[{record.levelname}]{colorama.Style.RESET_ALL}"
                return super().format(record)
            except Exception:
                return str(record.msg)

    formatter = CustomFormatter("%(levelname)s %(message)s")

    class InfoFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return record.levelno <= logging.INFO

    stdout_log = logging.StreamHandler(sys.stderr)
    stdout_log.setLevel(logging.DEBUG)
    stdout_log.addFilter(InfoFilter())
    stdout_log.setFormatter(formatter)
    LOG_ROOT.addHandler(stdout_log)


def on_exception(ex_type: type[BaseException], ex_value: BaseException, ex_traceback: TracebackType | None) -> None:
    """Handle uncaught exceptions."""
    if issubclass(ex_type, KeyboardInterrupt):
        sys.__excepthook__(ex_type, ex_value, ex_traceback)
    elif LOG.isEnabledFor(logging.DEBUG):
        LOG.error("", exc_info=(ex_type, ex_value, ex_traceback))
    else:
        LOG.error("%s: %s", ex_type.__name__, ex_value)


def on_exit() -> None:
    """Handle cleanup on exit."""
    for handler in LOG_ROOT.handlers:
        handler.flush()


def on_sigint(_sig: int, _frame: FrameType | None) -> None:
    """Handle interrupt signal."""
    LOG.warning(_("Aborted on user request."))
    sys.exit(1)


def load_dict(filepath:str, content_label:str = "") -> dict[str, Any]:
    """
    :raises FileNotFoundError
    """
    data = load_dict_if_exists(filepath, content_label)
    if data is None:
        raise FileNotFoundError(filepath)
    return data


def load_dict_if_exists(filepath:str, content_label:str = "") -> dict[str, Any] | None:
    abs_filepath = os.path.abspath(filepath)
    LOG.info("Loading %s[%s]...", content_label and content_label + _(" from ") or "", abs_filepath)

    __, file_ext = os.path.splitext(filepath)
    if file_ext not in (".json", ".yaml", ".yml"):
        raise ValueError(_('Unsupported file type. The filename "%s" must end with *.json, *.yaml, or *.yml') % filepath)

    if not os.path.exists(filepath):
        return None

    with open(filepath, encoding = "utf-8") as file:
        return json.load(file) if filepath.endswith(".json") else YAML().load(file)  # type: ignore[no-any-return] # mypy


def load_dict_from_module(module:ModuleType, filename:str, content_label:str = "") -> dict[str, Any]:
    """
    :raises FileNotFoundError
    """
    LOG.debug("Loading %s[%s.%s]...", content_label and content_label + " from " or "", module.__name__, filename)

    __, file_ext = os.path.splitext(filename)
    if file_ext not in (".json", ".yaml", ".yml"):
        raise ValueError(f'Unsupported file type. The filename "{filename}" must end with *.json, *.yaml, or *.yml')

    content = get_resource_as_string(module, filename)  # pylint: disable=deprecated-method
    return json.loads(content) if filename.endswith(".json") else YAML().load(content)  # type: ignore[no-any-return] # mypy


def save_dict(filepath:str, content:dict[str, Any]) -> None:
    filepath = os.path.abspath(filepath)
    LOG.info("Saving [%s]...", filepath)
    with open(filepath, "w", encoding = "utf-8") as file:
        if filepath.endswith(".json"):
            file.write(json.dumps(content, indent = 2, ensure_ascii = False))
        else:
            yaml = YAML()
            yaml.indent(mapping = 2, sequence = 4, offset = 2)
            yaml.representer.add_representer(str,  # use YAML | block style for multi-line strings
                lambda dumper, data:
                    dumper.represent_scalar('tag:yaml.org,2002:str', data, style = '|' if '\n' in data else None)
            )
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


def calculate_content_hash(ad_cfg: dict[str, Any]) -> str:
    """Calculate a hash for user-modifiable fields of the ad."""

    # Relevant fields for the hash
    content = {
        "active": bool(ad_cfg.get("active", True)),  # Explicitly convert to bool
        "type": str(ad_cfg.get("type", "")),  # Explicitly convert to string
        "title": str(ad_cfg.get("title", "")),
        "description": str(ad_cfg.get("description", "")),
        "category": str(ad_cfg.get("category", "")),
        "price": str(ad_cfg.get("price", "")),  # Price always as string
        "price_type": str(ad_cfg.get("price_type", "")),
        "special_attributes": dict(ad_cfg.get("special_attributes") or {}),  # Handle None case
        "shipping_type": str(ad_cfg.get("shipping_type", "")),
        "shipping_costs": str(ad_cfg.get("shipping_costs", "")),
        "shipping_options": sorted([str(x) for x in (ad_cfg.get("shipping_options") or [])]),  # Handle None case
        "sell_directly": bool(ad_cfg.get("sell_directly", False)),  # Explicitly convert to bool
        "images": sorted([os.path.basename(str(img)) if img is not None else "" for img in (ad_cfg.get("images") or [])]),  # Handle None values in images
        "contact": {
            "name": str(ad_cfg.get("contact", {}).get("name", "")),
            "street": str(ad_cfg.get("contact", {}).get("street", "")),  # Changed from "None" to empty string for consistency
            "zipcode": str(ad_cfg.get("contact", {}).get("zipcode", "")),
            "phone": str(ad_cfg.get("contact", {}).get("phone", ""))
        }
    }

    # Create sorted JSON string for consistent hashes
    content_str = json.dumps(content, sort_keys=True)
    return hashlib.sha256(content_str.encode()).hexdigest()
