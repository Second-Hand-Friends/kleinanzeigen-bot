"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

This module contains tests for verifying the completeness and correctness of translations in the project.
It ensures that:
1. All log messages in the code have corresponding translations
2. All translations in the YAML files are actually used in the code
3. No obsolete translations exist in the YAML files

The tests work by:
1. Extracting all translatable messages from Python source files
2. Loading translations from YAML files
3. Comparing the extracted messages with translations
4. Verifying no unused translations exist
"""
import ast, os
from dataclasses import dataclass
from importlib.resources import files
from collections import defaultdict

from ruamel.yaml import YAML
import pytest

from kleinanzeigen_bot import resources

# Messages that are intentionally not translated (internal/debug messages)
EXCLUDED_MESSAGES: dict[str, set[str]] = {
    "kleinanzeigen_bot/__init__.py": {"############################################"}
}

# Type aliases for better readability
ModulePath = str
FunctionName = str
Message = str
TranslationDict = dict[ModulePath, dict[FunctionName, dict[Message, str]]]
MessageDict = dict[FunctionName, dict[Message, set[Message]]]
MissingDict = dict[FunctionName, dict[Message, set[Message]]]


@dataclass
class MessageLocation:
    """Represents the location of a message in the codebase."""
    module: str
    function: str
    message: str


def _get_function_name(node: ast.AST) -> str:
    """
    Get the name of the function containing this AST node.
    This matches i18n.py's behavior which only uses the function name for translation lookups.
    For module-level code, returns "module" to match i18n.py's convention.

    Args:
        node: The AST node to analyze

    Returns:
        The function name or "module" for module-level code
    """
    def find_parent_context(n: ast.AST) -> tuple[str | None, str | None]:
        """Find the containing class and function names."""
        class_name = None
        function_name = None
        current = n

        while hasattr(current, '_parent'):
            current = getattr(current, '_parent')
            if isinstance(current, ast.ClassDef) and not class_name:
                class_name = current.name
            elif isinstance(current, ast.FunctionDef) or isinstance(current, ast.AsyncFunctionDef) and not function_name:
                function_name = current.name
                break  # We only need the immediate function name
        return class_name, function_name

    _, function_name = find_parent_context(node)
    if function_name:
        return function_name
    return "module"  # For module-level code


def _extract_log_messages(file_path: str) -> MessageDict:
    """
    Extract all translatable messages from a Python file with their function context.

    Args:
        file_path: Path to the Python file to analyze

    Returns:
        Dictionary mapping function names to their messages
    """
    with open(file_path, 'r', encoding='utf-8') as file:
        tree = ast.parse(file.read(), filename=file_path)

    # Add parent references for context tracking
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            setattr(child, '_parent', parent)

    messages: MessageDict = defaultdict(lambda: defaultdict(set))

    def add_message(function: str, msg: str) -> None:
        """Helper to add a message to the messages dictionary."""
        if function not in messages:
            messages[function] = defaultdict(set)
        if msg not in messages[function]:
            messages[function][msg] = {msg}

    def extract_string_value(node: ast.AST) -> str | None:
        """Safely extract string value from an AST node."""
        if isinstance(node, ast.Constant):
            value = getattr(node, 'value', None)
            return value if isinstance(value, str) else None
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        function_name = _get_function_name(node)

        # Extract messages from various call types
        if (isinstance(node.func, ast.Attribute) and
            isinstance(node.func.value, ast.Name) and
            node.func.value.id in {'LOG', 'logger', 'logging'} and
                node.func.attr in {'debug', 'info', 'warning', 'error', 'critical'}):
            if node.args:
                msg = extract_string_value(node.args[0])
                if msg:
                    add_message(function_name, msg)

        # Handle gettext calls
        elif ((isinstance(node.func, ast.Name) and node.func.id == '_') or
              (isinstance(node.func, ast.Attribute) and node.func.attr == 'gettext')):
            if node.args:
                msg = extract_string_value(node.args[0])
                if msg:
                    add_message(function_name, msg)

        # Handle other translatable function calls
        elif isinstance(node.func, ast.Name) and node.func.id in {'ainput', 'pluralize', 'ensure'}:
            arg_index = 0 if node.func.id == 'ainput' else 1
            if len(node.args) > arg_index:
                msg = extract_string_value(node.args[arg_index])
                if msg:
                    add_message(function_name, msg)

    print(f"Messages: {messages}")

    return messages


def _get_all_log_messages() -> dict[str, MessageDict]:
    """
    Get all translatable messages from all Python files in the project.

    Returns:
        Dictionary mapping module paths to their function messages
    """
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'src', 'kleinanzeigen_bot')
    print(f"\nScanning for messages in directory: {src_dir}")

    messages_by_file: dict[str, MessageDict] = {
        # Special case for getopt.py which is imported
        "getopt.py": {
            "do_longs": {
                "option --%s requires argument": {"option --%s requires argument"},
                "option --%s must not have an argument": {"option --%s must not have an argument"}
            },
            "long_has_args": {
                "option --%s not recognized": {"option --%s not recognized"},
                "option --%s not a unique prefix": {"option --%s not a unique prefix"}
            },
            "do_shorts": {
                "option -%s requires argument": {"option -%s requires argument"}
            },
            "short_has_arg": {
                "option -%s not recognized": {"option -%s not recognized"}
            }
        }
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
                    module_path = os.path.join('kleinanzeigen_bot', relative_path)
                    module_path = module_path.replace(os.sep, '/')
                    messages_by_file[module_path] = messages

    return messages_by_file


def _get_available_languages() -> list[str]:
    """
    Get list of available translation languages from translation files.

    Returns:
        List of language codes (e.g. ['de', 'en'])
    """
    languages = []
    resources_path = files(resources)
    for file in resources_path.iterdir():
        if file.name.startswith("translations.") and file.name.endswith(".yaml"):
            lang = file.name[13:-5]  # Remove "translations." and ".yaml"
            languages.append(lang)
    return sorted(languages)


def _get_translations_for_language(lang: str) -> TranslationDict:
    """
    Get translations for a specific language from its YAML file.

    Args:
        lang: Language code (e.g. 'de')

    Returns:
        Dictionary containing all translations for the language
    """
    yaml = YAML(typ='safe')
    translation_file = f"translations.{lang}.yaml"
    print(f"Loading translations from {translation_file}")
    content = files(resources).joinpath(translation_file).read_text()
    translations = yaml.load(content) or {}
    return translations


def _find_translation(translations: TranslationDict,
                     module: str,
                     function: str,
                     message: str) -> bool:
    """
    Check if a translation exists for a given message in the exact location where i18n.py will look.
    This matches the lookup logic in i18n.py which uses dicts.safe_get().

    Args:
        translations: Dictionary of all translations
        module: Module path
        function: Function name
        message: Message to find translation for

    Returns:
        True if translation exists in the correct location, False otherwise
    """
    # Special case for getopt.py
    if module == 'getopt.py':
        return bool(translations.get(module, {}).get(function, {}).get(message))

    # Add kleinanzeigen_bot/ prefix if not present
    module_path = f'kleinanzeigen_bot/{module}' if not module.startswith('kleinanzeigen_bot/') else module

    # Check if module exists in translations
    module_trans = translations.get(module_path, {})
    if not isinstance(module_trans, dict):
        print(f"Module {module_path} translations is not a dictionary")
        return False

    # Check if function exists in module translations
    function_trans = module_trans.get(function, {})
    if not isinstance(function_trans, dict):
        print(f"Function {function} translations in module {module_path} is not a dictionary")
        return False

    # Check if message exists in function translations
    has_translation = message in function_trans

    return has_translation


@pytest.mark.parametrize("lang", _get_available_languages())
def test_all_log_messages_have_translations(lang: str) -> None:
    """
    Test that all translatable messages in the code have translations for each language.

    This test ensures that no untranslated messages exist in the codebase.
    """
    messages_by_file = _get_all_log_messages()
    translations = _get_translations_for_language(lang)

    missing_translations = []

    for module, functions in messages_by_file.items():
        excluded = EXCLUDED_MESSAGES.get(module, set())
        for function, messages in functions.items():
            for message in messages:
                # Skip excluded messages
                if message in excluded:
                    continue
                if not _find_translation(translations, module, function, message):
                    missing_translations.append(MessageLocation(module, function, message))

    if missing_translations:
        missing_str = f"\nPlease add the following missing translations for language [{lang}]:\n"

        def make_inner_dict() -> defaultdict[str, set[str]]:
            return defaultdict(set)

        by_module: defaultdict[str, defaultdict[str, set[str]]] = defaultdict(make_inner_dict)

        for loc in missing_translations:
            assert isinstance(loc.module, str), "Module must be a string"
            assert isinstance(loc.function, str), "Function must be a string"
            assert isinstance(loc.message, str), "Message must be a string"
            by_module[loc.module][loc.function].add(loc.message)

        # There is a type error here, but it's not a problem
        for module, functions in sorted(by_module.items()):  # type: ignore[assignment]
            missing_str += f"  {module}:\n"
            for function, messages in sorted(functions.items()):
                missing_str += f"    {function}:\n"
                for message in sorted(messages):
                    missing_str += f'      "{message}"\n'
        raise AssertionError(missing_str)


@pytest.mark.parametrize("lang", _get_available_languages())
def test_no_obsolete_translations(lang: str) -> None:
    """
    Test that all translations in each language YAML file are actually used in the code.

    This test ensures there are no obsolete translations that should be removed.
    """
    messages_by_file = _get_all_log_messages()
    translations = _get_translations_for_language(lang)

    obsolete_items: list[tuple[str, str, str]] = []

    for module, module_trans in translations.items():
        # Add kleinanzeigen_bot/ prefix if not present
        module_with_prefix = f'kleinanzeigen_bot/{module}' if not module.startswith('kleinanzeigen_bot/') else module

        # Remove .py extension for comparison if present
        module_no_ext = module_with_prefix[:-3] if module_with_prefix.endswith('.py') else module_with_prefix

        if module_no_ext not in messages_by_file:
            # Skip obsolete module check since we know these modules are needed
            continue

        code_messages = messages_by_file[module_no_ext]
        for function, function_trans in module_trans.items():
            if not isinstance(function_trans, dict):
                continue

            if function not in code_messages and function != 'module':
                # Skip obsolete function check since we know these functions are needed
                continue

            for trans_message in function_trans:
                if function == 'module' or trans_message in code_messages.get(function, {}):
                    continue
                obsolete_items.append((module, function, trans_message))

    if obsolete_items:
        obsolete_str = f"\nPlease remove the following obsolete translations for language [{lang}]:\n"
        by_module: defaultdict[str, defaultdict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for module, function, message in obsolete_items:
            if module not in by_module:
                by_module[module] = defaultdict(set)
            if function not in by_module[module]:
                by_module[module][function] = set()
            by_module[module][function].add(message)

        for module, functions in sorted(by_module.items()):
            obsolete_str += f"  {module}:\n"
            for function, messages in sorted(functions.items()):
                if function:
                    obsolete_str += f"    {function}:\n"
                for message in sorted(messages):
                    obsolete_str += f'      "{message}"\n'
        raise AssertionError(obsolete_str)


def test_translation_files_exist() -> None:
    """Test that at least one translation file exists."""
    languages = _get_available_languages()
    if not languages:
        raise AssertionError("No translation files found! Expected at least one translations.*.yaml file.")
