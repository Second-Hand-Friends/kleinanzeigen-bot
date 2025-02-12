"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import ast
import os
from importlib.resources import files

from ruamel.yaml import YAML
import pytest

from kleinanzeigen_bot import resources

# Messages that are intentionally not translated (internal/debug messages)
EXCLUDED_MESSAGES: dict[str, set[str]] = {}


def _extract_log_messages(file_path: str) -> set[str]:
    """Extract all log messages from a Python file."""
    with open(file_path, 'r', encoding='utf-8') as file:
        tree = ast.parse(file.read(), filename=file_path)

    messages = set()
    for node in ast.walk(tree):
        # Look for logging calls like LOG.info("message")
        if (isinstance(node, ast.Call) and
            isinstance(node.func, ast.Attribute) and
            isinstance(node.func.value, ast.Name) and
            node.func.value.id in {'LOG', 'logger', 'logging'} and
                node.func.attr in {'debug', 'info', 'warning', 'error', 'critical'}):

            # Extract the message from the first argument if it's a string literal
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                message = node.args[0].value
                if message:
                    messages.add(message)

        # Look for gettext calls like _("message")
        elif (isinstance(node, ast.Call) and
              ((isinstance(node.func, ast.Name) and node.func.id == '_') or
               (isinstance(node.func, ast.Attribute) and node.func.attr == 'gettext'))):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                message = node.args[0].value
                if message:
                    messages.add(message)

        # Look for ainput calls like await ainput("message")
        elif (isinstance(node, ast.Call) and
              isinstance(node.func, ast.Name) and
              node.func.id == 'ainput'):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                message = node.args[0].value
                if message:
                    messages.add(message)

        # Look for pluralize calls like pluralize("message", count)
        elif (isinstance(node, ast.Call) and
              isinstance(node.func, ast.Name) and
              node.func.id == 'pluralize'):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                message = node.args[0].value
                if message:
                    messages.add(message)

        # Look for ensure calls like ensure(condition, "message")
        elif (isinstance(node, ast.Call) and
              isinstance(node.func, ast.Name) and
              node.func.id == 'ensure'):
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                message = node.args[1].value
                if message:
                    messages.add(message)

    return messages


def _get_all_log_messages() -> dict[str, set[str]]:
    """Get all log messages from all Python files in the project."""
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'src', 'kleinanzeigen_bot')
    # getopt.py is not a module and therefore the original log messages are not found.
    messages_by_file: dict[str, set[str]] = {
        "getopt.py": {
            "option --%s requires argument",
            "option --%s must not have an argument",
            "option --%s not recognized",
            "option --%s not a unique prefix",
            "option -%s requires argument",
            "option -%s not recognized"
        },
    }

    for root, _, filenames in os.walk(src_dir):
        for filename in filenames:
            if filename.endswith('.py'):
                file_path = os.path.join(root, filename)
                relative_path = os.path.relpath(file_path, src_dir)
                if relative_path.startswith('resources/'):
                    continue
                messages = _extract_log_messages(file_path)
                if messages:
                    # Convert path to module format as used in translations.de.yaml
                    module_path = f"kleinanzeigen_bot/{relative_path}"
                    # Merge with any existing messages for this module
                    messages_by_file[module_path] = messages_by_file.get(module_path, set()) | messages

    return messages_by_file


def _get_available_languages() -> list[str]:
    """Get list of available translation languages from translation files."""
    languages = []
    resources_path = files(resources)
    for file in resources_path.iterdir():
        if file.name.startswith("translations.") and file.name.endswith(".yaml"):
            # Extract language code from filename (e.g., "translations.de.yaml" -> "de")
            lang = file.name[13:-5]  # Remove "translations." and ".yaml"
            languages.append(lang)
    return sorted(languages)


def _get_translations_for_language(lang: str) -> dict[str, dict[str, dict[str, str]]]:
    """Get translations for a specific language from its YAML file."""
    yaml = YAML(typ='safe')
    content = files(resources).joinpath(f"translations.{lang}.yaml").read_text()
    return yaml.load(content) or {}


def _find_translation(translations: dict[str, dict[str, dict[str, str]]], module: str, message: str) -> bool:
    """Check if a translation exists for a given message in a module."""
    # Get the module translations
    module_trans = translations.get(module, {})

    # Check all functions in the module
    for function_trans in module_trans.values():
        if not isinstance(function_trans, dict):
            continue
        if message in function_trans:
            return True
    return False


def _get_all_translations(translations: dict[str, dict[str, dict[str, str]]]) -> dict[str, set[str]]:
    """Get all translations organized by module."""
    result: dict[str, set[str]] = {}
    for module, functions in translations.items():
        if not isinstance(functions, dict):
            continue
        module_messages: set[str] = set()
        for function_trans in functions.values():
            if not isinstance(function_trans, dict):
                continue
            module_messages.update(function_trans.keys())
        if module_messages:
            result[module] = module_messages
    return result


@pytest.mark.parametrize("lang", _get_available_languages())
def test_all_log_messages_have_translations(lang: str) -> None:
    """Test that all log messages have translations for each language."""
    messages_by_file = _get_all_log_messages()
    translations = _get_translations_for_language(lang)

    missing_translations = []

    for module, messages in messages_by_file.items():
        excluded = EXCLUDED_MESSAGES.get(module, set())
        for message in messages:
            # Skip excluded messages
            if message in excluded:
                continue
            if not _find_translation(translations, module, message):
                missing_translations.append((module, message))

    if missing_translations:
        missing_str = f"\nPlease add the following missing translations for language [{lang}]:\n"
        module_dict: dict[str, set[str]] = {}
        for module, message in missing_translations:
            if module not in module_dict:
                module_dict[module] = set()
            module_dict[module].add(message)
        for module, messages in module_dict.items():
            missing_str += f"  {module}:\n"
            for message in messages:
                missing_str += f"    \"{message}\"\n"
        raise AssertionError(missing_str)


@pytest.mark.parametrize("lang", _get_available_languages())
def test_no_obsolete_translations(lang: str) -> None:
    """Test that all translations in each language YAML file are used in the code."""
    messages_by_file = _get_all_log_messages()
    translations = _get_translations_for_language(lang)
    translations_by_module = _get_all_translations(translations)

    obsolete_items: list[tuple[str, str]] = []

    # Check for obsolete modules
    for module in translations_by_module:
        if module not in messages_by_file:
            obsolete_items.append((module, f"Module '{module}' is obsolete and should be removed."))
            continue

        # Check for obsolete translations within valid modules
        code_messages = messages_by_file[module]
        for trans_message in translations_by_module[module]:
            if trans_message not in code_messages:
                obsolete_items.append((module, trans_message))

        # Check for empty functions in the translation file
        functions = translations.get(module, {})
        for func_name, func_trans in functions.items():
            if not isinstance(func_trans, dict) or not func_trans:
                obsolete_items.append((module, f"Function '{func_name}' is empty and should be removed."))

    if obsolete_items:
        obsolete_str = f"\nPlease remove the following obsolete translations for language [{lang}]:\n"
        by_module: dict[str, set[str]] = {}
        for module, message in obsolete_items:
            by_module.setdefault(module, set()).add(message)

        for module, messages in sorted(by_module.items()):
            obsolete_str += f"  {module}:\n"
            for message in sorted(messages):
                obsolete_str += f"    \"{message}\"\n"
        raise AssertionError(obsolete_str)


def test_translation_files_exist() -> None:
    """Test that at least one translation file exists."""
    languages = _get_available_languages()
    if not languages:
        raise AssertionError("No translation files found! Expected at least one translations.*.yaml file.")
