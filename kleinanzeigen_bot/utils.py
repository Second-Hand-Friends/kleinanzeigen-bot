"""
Copyright (C) 2021 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import copy, json, logging, random, os, sys, traceback, time
from importlib.resources import read_text as get_resource_as_string
from types import ModuleType
from typing import Any, Dict, Final, Iterable, Optional, Union

import coloredlogs, inflect
from ruamel.yaml import YAML

LOG_ROOT:Final[logging.Logger] = logging.getLogger()
LOG:Final[logging.Logger] = logging.getLogger("kleinanzeigen_bot.utils")


def is_frozen() -> bool:
    return getattr(sys, 'frozen', False)


def apply_defaults(target:Dict[Any, Any], defaults:Dict[Any, Any], attribute_filter = lambda _k, _v: True) -> Dict[Any, Any]:
    for key, default_value in defaults.items():
        if key in target:
            if isinstance(target[key], Dict) and isinstance(default_value, Dict):
                apply_defaults(target[key], default_value, attribute_filter)
        else:
            if attribute_filter(key, default_value):
                target[key] = copy.deepcopy(default_value)
    return target


def safe_get(a_map:Dict[Any, Any], *keys:str) -> Any:
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
    stdout_log.setFormatter(coloredlogs.ColoredFormatter('[%(levelname)s] %(message)s'))
    stdout_log.addFilter(type("", (logging.Filter,), {
        "filter": lambda rec: rec.levelno <= logging.INFO
    }))
    LOG_ROOT.addHandler(stdout_log)

    stderr_log = logging.StreamHandler(sys.stderr)
    stderr_log.setLevel(logging.WARNING)
    stderr_log.setFormatter(coloredlogs.ColoredFormatter('[%(levelname)s] %(message)s'))
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
    LOG.warning('Aborted on user request.')
    sys.exit(0)


def pause(min_ms:int = 200, max_ms:int = None) -> None:
    duration = random.randint(min_ms, max_ms is None and 2000 or max_ms)
    LOG.log(logging.INFO if duration > 1500 else logging.DEBUG, " ... pausing for %d ms ...", duration)
    time.sleep(duration / 1000)


def pluralize(word:str, count:Union[int, Iterable], prefix = True):
    if not hasattr(pluralize, "inflect"):
        pluralize.inflect = inflect.engine()
    if isinstance(count, Iterable):
        count = len(count)
    plural = pluralize.inflect.plural_noun(word, count)
    if prefix:
        return f'{count} {plural}'
    return plural


def load_dict(filepath:str, content_label:str = "", must_exist = True) -> Optional[Dict[str, Any]]:
    filepath = os.path.abspath(filepath)
    LOG.info("Loading %s[%s]...", content_label and content_label + " from " or "", filepath)

    _, file_ext = os.path.splitext(filepath)
    if not file_ext in [ ".json", ".yaml" , ".yml" ]:
        raise ValueError(f'Unsupported file type. The file name "{filepath}" must end with *.json, *.yaml, or *.yml')

    if not os.path.exists(filepath):
        if must_exist:
            raise FileNotFoundError(filepath)
        return None

    with open(filepath, encoding = "utf-8") as file:
        return json.load(file) if filepath.endswith(".json") else YAML().load(file)


def load_dict_from_module(module:ModuleType, filename:str, content_label:str = "", must_exist = True) -> Optional[Dict[str, Any]]:
    LOG.debug("Loading %s[%s.%s]...", content_label and content_label + " from " or "", module.__name__, filename)

    _, file_ext = os.path.splitext(filename)
    if not file_ext in [ ".json", ".yaml" , ".yml" ]:
        raise ValueError(f'Unsupported file type. The file name "{filename}" must end with *.json, *.yaml, or *.yml')

    try:
        content = get_resource_as_string(module, filename)
    except FileNotFoundError as ex:
        if must_exist:
            raise ex
        return None

    return json.loads(content) if filename.endswith(".json") else YAML().load(content)


def save_dict(filepath:str, content:Dict[str, Any]) -> None:
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
