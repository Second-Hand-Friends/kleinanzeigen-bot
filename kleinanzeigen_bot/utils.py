"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import copy, json, logging, os, secrets, sys, traceback, time
from importlib.resources import read_text as get_resource_as_string
from collections.abc import Iterable
from types import ModuleType
from typing import Any, Final

import coloredlogs, inflect
from ruamel.yaml import YAML

LOG_ROOT:Final[logging.Logger] = logging.getLogger()
LOG:Final[logging.Logger] = logging.getLogger("kleinanzeigen_bot.utils")


def ensure(condition:bool, error_message:str) -> None:
    """
    :raises AssertionError: if condition is False
    """
    if not condition:
        raise AssertionError(error_message)


def is_frozen() -> bool:
    """
    >>> is_frozen()
    False
    """
    return getattr(sys, "frozen", False)


def apply_defaults(target:dict[Any, Any], defaults:dict[Any, Any], ignore = lambda _k, _v: False, override = lambda _k, _v: False) -> dict[Any, Any]:
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


def on_exception(ex_type, ex_value, ex_traceback) -> None:
    if issubclass(ex_type, KeyboardInterrupt):
        sys.__excepthook__(ex_type, ex_value, ex_traceback)
        return
    if LOG.isEnabledFor(logging.DEBUG) or isinstance(ex_value, (AttributeError, ImportError, NameError)):
        LOG.error("".join(traceback.format_exception(ex_type, ex_value, ex_traceback)))
    elif isinstance(ex_value, AssertionError):
        LOG.error(ex_value)
    else:
        LOG.error("%s: %s", ex_type.__name__, ex_value)


def on_exit() -> None:
    for handler in LOG_ROOT.handlers:
        handler.flush()


def on_sigint(_sig:int, _frame) -> None:
    LOG.warning("Aborted on user request.")
    sys.exit(0)


def pause(min_ms:int = 200, max_ms:int = 2000) -> None:
    if min_ms == max_ms:
        duration = min_ms
    else:
        duration = secrets.randbelow(max_ms - min_ms) + min_ms
    LOG.log(logging.INFO if duration > 1500 else logging.DEBUG, " ... pausing for %d ms ...", duration)
    time.sleep(duration / 1000)


def pluralize(word:str, count:int | Iterable, prefix = True):
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
    if isinstance(count, Iterable):
        count = len(count)
    plural = pluralize.inflect.plural_noun(word, count)
    if prefix:
        return f"{count} {plural}"
    return plural


def load_dict(filepath:str, content_label:str = "", must_exist = True) -> dict[str, Any] | None:
    filepath = os.path.abspath(filepath)
    LOG.info("Loading %s[%s]...", content_label and content_label + " from " or "", filepath)

    _, file_ext = os.path.splitext(filepath)
    if file_ext not in [".json", ".yaml", ".yml"]:
        raise ValueError(f'Unsupported file type. The file name "{filepath}" must end with *.json, *.yaml, or *.yml')

    if not os.path.exists(filepath):
        if must_exist:
            raise FileNotFoundError(filepath)
        return None

    with open(filepath, encoding = "utf-8") as file:
        return json.load(file) if filepath.endswith(".json") else YAML().load(file)


def load_dict_from_module(module:ModuleType, filename:str, content_label:str = "", must_exist = True) -> dict[str, Any] | None:
    LOG.debug("Loading %s[%s.%s]...", content_label and content_label + " from " or "", module.__name__, filename)

    _, file_ext = os.path.splitext(filename)
    if file_ext not in [".json", ".yaml", ".yml"]:
        raise ValueError(f'Unsupported file type. The file name "{filename}" must end with *.json, *.yaml, or *.yml')

    try:
        content = get_resource_as_string(module, filename)
    except FileNotFoundError as ex:
        if must_exist:
            raise ex
        return None

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
