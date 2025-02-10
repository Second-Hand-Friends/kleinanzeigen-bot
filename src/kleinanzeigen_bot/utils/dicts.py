"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import copy, json, os
from collections.abc import Callable
from importlib.resources import read_text as get_resource_as_string
from gettext import gettext as _
from types import ModuleType
from typing import Any, Final

from ruamel.yaml import YAML
from . import files, loggers  # pylint: disable=cyclic-import

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)


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


def load_dict(filepath:str, content_label:str = "") -> dict[str, Any]:
    """
    :raises FileNotFoundError
    """
    data = load_dict_if_exists(filepath, content_label)
    if data is None:
        raise FileNotFoundError(filepath)
    return data


def load_dict_if_exists(filepath:str, content_label:str = "") -> dict[str, Any] | None:
    abs_filepath = files.abspath(filepath)
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
    filepath = files.abspath(filepath)
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
