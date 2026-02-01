# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import copy, json, os, unicodedata  # isort: skip
from collections import defaultdict
from collections.abc import Callable
from gettext import gettext as _
from importlib.resources import read_text as get_resource_as_string
from pathlib import Path
from types import ModuleType
from typing import Any, Final, TypeVar, cast

from ruamel.yaml import YAML

from . import files, loggers  # pylint: disable=cyclic-import

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

# https://mypy.readthedocs.io/en/stable/generics.html#generic-functions
K = TypeVar("K")
V = TypeVar("V")


def apply_defaults(
    target:dict[Any, Any],
    defaults:dict[Any, Any],
    ignore:Callable[[Any, Any], bool] = lambda _k, _v: False,
    override:Callable[[Any, Any], bool] = lambda _k, _v: False,
) -> dict[Any, Any]:
    """
    >>> apply_defaults({}, {'a': 'b'})
    {'a': 'b'}
    >>> apply_defaults({'a': 'b'}, {'a': 'c'})
    {'a': 'b'}
    >>> apply_defaults({'a': ''}, {'a': 'b'})
    {'a': ''}
    >>> apply_defaults({}, {'a': 'b'}, ignore = lambda k, _: k == 'a')
    {}
    >>> apply_defaults({'a': ''}, {'a': 'b'}, override = lambda _, v: v == '')
    {'a': 'b'}
    >>> apply_defaults({'a': None}, {'a': 'b'}, override = lambda _, v: v == '')
    {'a': None}
    >>> apply_defaults({'a': {'x': 1}}, {'a': {'x': 0, 'y': 2}})
    {'a': {'x': 1, 'y': 2}}
    >>> apply_defaults({'a': {'b': False}}, {'a': { 'b': True}})
    {'a': {'b': False}}
    """
    for key, default_value in defaults.items():
        if key in target:
            if isinstance(target[key], dict) and isinstance(default_value, dict):
                apply_defaults(target = target[key], defaults = default_value, ignore = ignore, override = override)
            elif override(key, target[key]):  # force overwrite if override says so
                target[key] = copy.deepcopy(default_value)
        elif not ignore(key, default_value):  # only set if not explicitly ignored
            target[key] = copy.deepcopy(default_value)
    return target


def defaultdict_to_dict(d:defaultdict[K, V]) -> dict[K, V]:
    """Recursively convert defaultdict to dict."""
    result:dict[K, V] = {}
    for key, value in d.items():
        if isinstance(value, defaultdict):
            result[key] = defaultdict_to_dict(value)  # type: ignore[assignment]
        else:
            result[key] = value
    return result


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
    LOG.info("Loading %s[%s]...", content_label and content_label + " from " or "", abs_filepath)

    __, file_ext = os.path.splitext(filepath)
    if file_ext not in {".json", ".yaml", ".yml"}:
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
    if file_ext not in {".json", ".yaml", ".yml"}:
        raise ValueError(f'Unsupported file type. The filename "{filename}" must end with *.json, *.yaml, or *.yml')

    content = get_resource_as_string(module, filename)  # pylint: disable=deprecated-method
    return json.loads(content) if filename.endswith(".json") else YAML().load(content)  # type: ignore[no-any-return] # mypy


def _configure_yaml() -> YAML:
    """
    Configure and return a YAML instance with standard settings.

    Returns:
        Configured YAML instance ready for dumping
    """
    yaml = YAML()
    yaml.indent(mapping = 2, sequence = 4, offset = 2)
    yaml.representer.add_representer(
        str,  # use YAML | block style for multi-line strings
        lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:str", data, style = "|" if "\n" in data else None),
    )
    yaml.allow_duplicate_keys = False
    yaml.explicit_start = False
    return yaml


def save_dict(filepath:str | Path, content:dict[str, Any], *, header:str | None = None) -> None:
    # Normalize filepath to NFC for cross-platform consistency (issue #728)
    # Ensures file paths match NFC-normalized directory names from sanitize_folder_name()
    # Also handles edge cases where paths don't originate from sanitize_folder_name()
    filepath = Path(unicodedata.normalize("NFC", str(filepath)))

    # Create parent directory if needed
    filepath.parent.mkdir(parents = True, exist_ok = True)

    LOG.info("Saving [%s]...", filepath)
    with open(filepath, "w", encoding = "utf-8") as file:
        if header:
            file.write(header)
            file.write("\n")
        if filepath.suffix == ".json":
            file.write(json.dumps(content, indent = 2, ensure_ascii = False))
        else:
            yaml = _configure_yaml()
            yaml.dump(content, file)


def safe_get(a_map:dict[Any, Any], *keys:str) -> Any:
    """
    >>> safe_get({"foo": {}}, "foo", "bar") is None
    True
    >>> safe_get({"foo": {"bar": "some_value"}}, "foo", "bar")
    'some_value'
    """
    if a_map:
        try:
            for key in keys:
                a_map = a_map[key]
        except (KeyError, TypeError):
            return None
    return a_map


def _should_exclude(field_name:str, exclude:set[str] | dict[str, Any] | None) -> bool:
    """Check if a field should be excluded based on exclude rules."""
    if exclude is None:
        return False
    if isinstance(exclude, set):
        return field_name in exclude
    if isinstance(exclude, dict):
        # If the value is None, it means exclude this field entirely
        # If the value is a dict/set, it means nested exclusion rules
        if field_name in exclude:
            return exclude[field_name] is None
    return False


def _get_nested_exclude(field_name:str, exclude:set[str] | dict[str, Any] | None) -> set[str] | dict[str, Any] | None:
    """Get nested exclude rules for a field."""
    if exclude is None:
        return None
    if isinstance(exclude, dict) and field_name in exclude:
        nested = exclude[field_name]
        # If nested is None, it means exclude entirely - no nested rules to pass down
        # If nested is a set or dict, pass it down as nested exclusion rules
        if nested is None:
            return None
        return cast(set[str] | dict[str, Any], nested)
    return None


def model_to_commented_yaml(
    model_instance:Any,
    *,
    indent_level:int = 0,
    exclude:set[str] | dict[str, Any] | None = None,
) -> Any:
    """
    Convert a Pydantic model instance to a structure with YAML comments.

    This function recursively processes a Pydantic model and creates a
    CommentedMap/CommentedSeq structure with comments based on field descriptions.
    The comments are added as block comments above each field.

    Args:
        model_instance: A Pydantic model instance to convert
        indent_level: Current indentation level (for recursive calls)
        exclude: Optional set of field names to exclude, or dict for nested exclusion

    Returns:
        A CommentedMap, CommentedSeq, or primitive value suitable for YAML output

    Example:
        >>> from pydantic import BaseModel, Field
        >>> class Config(BaseModel):
        ...     name: str = Field(default="test", description="The name")
        >>> config = Config()
        >>> result = model_to_commented_yaml(config)
    """
    # Delayed import to avoid circular dependency
    from pydantic import BaseModel  # noqa: PLC0415
    from ruamel.yaml.comments import CommentedMap, CommentedSeq  # noqa: PLC0415

    # Handle primitive types
    if model_instance is None or isinstance(model_instance, (str, int, float, bool)):
        return model_instance

    # Handle lists/sequences
    if isinstance(model_instance, (list, tuple)):
        seq = CommentedSeq()
        for item in model_instance:
            seq.append(model_to_commented_yaml(item, indent_level = indent_level + 1, exclude = exclude))
        return seq

    # Handle dictionaries (not from Pydantic models)
    if isinstance(model_instance, dict) and not isinstance(model_instance, BaseModel):
        cmap = CommentedMap()
        for key, value in model_instance.items():
            if _should_exclude(key, exclude):
                continue
            cmap[key] = model_to_commented_yaml(value, indent_level = indent_level + 1, exclude = exclude)
        return cmap

    # Handle Pydantic models
    if isinstance(model_instance, BaseModel):
        cmap = CommentedMap()
        model_class = model_instance.__class__
        field_count = 0

        # Get field information from the model class
        for field_name, field_info in model_class.model_fields.items():
            # Skip excluded fields
            if _should_exclude(field_name, exclude):
                continue

            # Get the value from the instance, handling unset required fields
            try:
                value = getattr(model_instance, field_name)
            except AttributeError:
                # Field is not set (e.g., required field with no default)
                continue

            # Add visual separators
            if indent_level == 0 and field_count > 0:
                # Major section: blank line + prominent separator with 80 # characters
                cmap.yaml_set_comment_before_after_key(field_name, before = "\n" + "#" * 80, indent = 0)
            elif indent_level > 0:
                # Nested fields: always add blank line separator (both between siblings and before first child)
                cmap.yaml_set_comment_before_after_key(field_name, before = "", indent = 0)

            # Get nested exclude rules for this field
            nested_exclude = _get_nested_exclude(field_name, exclude)

            # Process the value recursively
            processed_value = model_to_commented_yaml(value, indent_level = indent_level + 1, exclude = nested_exclude)
            cmap[field_name] = processed_value
            field_count += 1

            # Build comment from description and examples
            comment_parts = []

            # Add description if available
            description = field_info.description
            if description:
                comment_parts.append(description)

            # Add examples if available
            examples = field_info.examples
            if examples:
                # Check if this is a list field by looking at the value type
                is_list_field = isinstance(value, list)

                if is_list_field:
                    # For list fields, show YAML syntax with field name for clarity
                    examples_lines = [
                        "Example usage:",
                        f"  {field_name}:",
                        *[f"    - {ex}" for ex in examples]
                    ]
                    comment_parts.append("\n".join(examples_lines))
                elif len(examples) == 1:
                    # Single example for scalar field: use singular form without list marker
                    comment_parts.append(f"Example: {examples[0]}")
                else:
                    # Multiple examples for scalar field: show as alternatives (not list items)
                    # Use bullets (•) instead of hyphens to distinguish from YAML list syntax
                    examples_lines = ["Examples (choose one):", *[f"  • {ex}" for ex in examples]]
                    comment_parts.append("\n".join(examples_lines))

            # Set the comment above the key
            if comment_parts:
                full_comment = "\n".join(comment_parts)
                cmap.yaml_set_comment_before_after_key(field_name, before = full_comment, indent = indent_level * 2)

        return cmap

    # Fallback: return as-is
    return model_instance


def save_commented_model(
    filepath:str | Path,
    model_instance:Any,
    *,
    header:str | None = None,
    exclude:set[str] | dict[str, Any] | None = None,
) -> None:
    """
    Save a Pydantic model to a YAML file with field descriptions as comments.

    This function converts a Pydantic model to a commented YAML structure
    where each field has its description (and optionally examples) as a
    block comment above the key.

    Args:
        filepath: Path to the output YAML file
        model_instance: Pydantic model instance to save
        header: Optional header string to write at the top of the file
        exclude: Optional set of field names to exclude, or dict for nested exclusion

    Example:
        >>> from kleinanzeigen_bot.model.config_model import Config
        >>> from pathlib import Path
        >>> import tempfile
        >>> config = Config()
        >>> with tempfile.TemporaryDirectory() as tmpdir:
        ...     save_commented_model(Path(tmpdir) / "config.yaml", config, header="# Config file")
    """
    filepath = Path(unicodedata.normalize("NFC", str(filepath)))
    filepath.parent.mkdir(parents = True, exist_ok = True)

    LOG.info("Saving [%s]...", filepath)

    # Convert to commented structure directly from model (preserves metadata)
    commented_data = model_to_commented_yaml(model_instance, exclude = exclude)

    with open(filepath, "w", encoding = "utf-8") as file:
        if header:
            file.write(header)
            file.write("\n")

        yaml = _configure_yaml()
        yaml.dump(commented_data, file)
