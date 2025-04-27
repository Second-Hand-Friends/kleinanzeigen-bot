# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import ast, logging, re, sys  # isort: skip
from pathlib import Path
from typing import Final, List, Protocol, Tuple

from typing_extensions import override

# Configure basic logging
logging.basicConfig(level = logging.INFO, format = "%(levelname)s: %(message)s")
LOG:Final[logging.Logger] = logging.getLogger(__name__)


class FormatterRule(Protocol):
    """
    A code processor that can modify source lines based on the AST.
    """

    def apply(self, tree:ast.AST, lines:List[str], path:Path) -> List[str]:
        ...


class NoSpaceAfterColonInTypeAnnotationRule(FormatterRule):
    """
    Removes whitespace between the colon (:) and the type annotation in variable and function parameter declarations.

    This rule enforces `a:int` instead of `a: int`.
    It is the opposite behavior of autopep8 rule E231.

    Example:
        # Before
        def foo(a: int, b : str) -> None:
            pass

        # After
        def foo(a:int, b:str) -> None:
            pass
    """

    @override
    def apply(self, tree:ast.AST, lines:List[str], path:Path) -> List[str]:
        ann_positions:List[Tuple[int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.arg) and node.annotation is not None:
                ann_positions.append((node.annotation.lineno - 1, node.annotation.col_offset))
            elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
                ann = node.annotation
                ann_positions.append((ann.lineno - 1, ann.col_offset))

        if not ann_positions:
            return lines

        new_lines:List[str] = []
        for idx, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                new_lines.append(line)
                continue

            chars = list(line)
            offsets = [col for (lin, col) in ann_positions if lin == idx]
            for col in sorted(offsets, reverse = True):
                prefix = "".join(chars[:col])
                colon_idx = prefix.rfind(":")
                if colon_idx == -1:
                    continue
                j = colon_idx + 1
                while j < len(chars) and chars[j].isspace():
                    del chars[j]
            new_lines.append("".join(chars))

        return new_lines


class EqualSignSpacingInDefaultsAndNamedArgsRule(FormatterRule):
    """
    Ensures that the '=' sign in default values for function parameters and keyword arguments in function calls
    is surrounded by exactly one space on each side.

    This rule enforces `a:int = 3` instead of `a:int=3`, and `x = 42` instead of `x=42` or `x =42`.
    It is the opposite behavior of autopep8 rule E251.

    Example:
        # Before
        def foo(a:int=3, b :str=  "bar"):
            pass

        foo(x=42,y = "hello")

        # After
        def foo(a:int = 3, b:str = "bar"):
            pass

        foo(x = 42, y = "hello")
    """

    @override
    def apply(self, tree:ast.AST, lines:List[str], path:Path) -> List[str]:
        equals_positions:List[Tuple[int, int]] = []
        for node in ast.walk(tree):
            # --- Defaults in function definitions, async defs & lambdas ---
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                # positional defaults
                equals_positions.extend(
                    (d.lineno - 1, d.col_offset)
                    for d in node.args.defaults
                    if d is not None
                )
                # keyword-only defaults (only on defs, not lambdas)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    equals_positions.extend(
                        (d.lineno - 1, d.col_offset)
                        for d in node.args.kw_defaults
                        if d is not None
                    )

            # --- Keyword arguments in calls ---
            if isinstance(node, ast.Call):
                equals_positions.extend(
                    (kw.value.lineno - 1, kw.value.col_offset)
                    for kw in node.keywords
                    if kw.arg is not None
                )

        if not equals_positions:
            return lines

        new_lines:List[str] = []
        for line_idx, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                new_lines.append(line)
                continue

            chars = list(line)
            equals_offsets = [col for (lineno, col) in equals_positions if lineno == line_idx]
            for col in sorted(equals_offsets, reverse = True):
                prefix = "".join(chars[:col])
                equal_sign_idx = prefix.rfind("=")
                if equal_sign_idx == -1:
                    continue

                # remove spaces before '='
                left_index = equal_sign_idx - 1
                while left_index >= 0 and chars[left_index].isspace():
                    del chars[left_index]
                    equal_sign_idx -= 1
                    left_index -= 1

                # remove spaces after '='
                right_index = equal_sign_idx + 1
                while right_index < len(chars) and chars[right_index].isspace():
                    del chars[right_index]

                # insert single spaces
                chars.insert(equal_sign_idx, " ")
                chars.insert(equal_sign_idx + 2, " ")
            new_lines.append("".join(chars))

        return new_lines


class PreferDoubleQuotesRule(FormatterRule):
    """
    Ensures string literals use double quotes unless the content contains a double quote.

    Example:
        # Before
        foo = 'hello'
        bar = 'a "quote" inside'

        # After
        foo = "hello"
        bar = 'a "quote" inside'  # kept as-is, because it contains a double quote
    """

    @override
    def apply(self, tree:ast.AST, lines:List[str], path:Path) -> List[str]:
        new_lines = lines.copy()

        # Track how much each line has shifted so far
        line_shifts:dict[int, int] = dict.fromkeys(range(len(lines)), 0)

        # Build a parent map for f-string detection
        parent_map:dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parent_map[child] = parent

        def is_in_fstring(node:ast.AST) -> bool:
            p = parent_map.get(node)
            while p:
                if isinstance(p, ast.JoinedStr):
                    return True
                p = parent_map.get(p)
            return False

        # Regex to locate a single- or triple-quoted literal:
        #   (?P<prefix>[rRbuUfF]*)  optional string flags (r, b, u, f, etc.), case-insensitive
        #   (?P<quote>'{3}|')       the opening delimiter: either three single-quotes (''') or one ('),
        #                           but never two in a row (so we won't mis-interpret adjacent quotes)
        #   (?P<content>.*?)        the literal's content, non-greedy up to the next same delimiter
        #   (?P=quote)              the matching closing delimiter (same length as the opener)
        literal_re = re.compile(
            r"(?P<prefix>[rRbuUfF]*)(?P<quote>'{3}|')(?P<content>.*?)(?P=quote)",
            re.DOTALL,
        )

        for node in ast.walk(tree):
            # only handle simple string constants
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue

            # skip anything inside an f-string, at any depth
            if is_in_fstring(node):
                continue

            starting_line_number = getattr(node, "lineno", None)
            starting_col_offset = getattr(node, "col_offset", None)
            if starting_line_number is None or starting_col_offset is None:
                continue

            start_line = starting_line_number - 1
            shift = line_shifts[start_line]
            raw = new_lines[start_line]
            # apply shift so we match against current edited line
            idx = starting_col_offset + shift
            if idx >= len(raw) or raw[idx] not in {"'", "r", "u", "b", "f", "R", "U", "B", "F"}:
                continue

            # match literal at that column
            m = literal_re.match(raw[idx:])
            if not m:
                continue

            prefix = m.group("prefix")
            quote = m.group("quote")  # either "'" or "'''"
            content = m.group("content")  # what's inside

            # skip if content has a double-quote already
            if '"' in content:
                continue

            # build new literal with the same prefix, but double‐quote delimiter
            delim = '"' * len(quote)
            escaped = content.replace(delim, "\\" + delim)
            new_literal = f"{prefix}{delim}{escaped}{delim}"

            literal_len = m.end()  # how many chars we're replacing
            before = raw[:idx]
            after = raw[idx + literal_len:]
            new_lines[start_line] = before + new_literal + after

            # record shift delta for any further edits on this line
            line_shifts[start_line] += len(new_literal) - literal_len

        return new_lines


FORMATTER_RULES:List[FormatterRule] = [
    NoSpaceAfterColonInTypeAnnotationRule(),
    EqualSignSpacingInDefaultsAndNamedArgsRule(),
    PreferDoubleQuotesRule(),
]


def format_file(path:Path) -> None:
    # Read without newline conversion
    with path.open("r", encoding = "utf-8", newline = "") as rf:
        original_text = rf.read()

    # Initial parse
    try:
        tree = ast.parse(original_text)
    except SyntaxError as e:
        LOG.error(
            "Syntax error parsing %s[%d:%d]: %r -> %s",
            path, e.lineno, e.offset, (e.text or "").rstrip(), e.msg
        )
        return

    lines = original_text.splitlines(keepends = True)
    formatted_text = original_text
    success = True
    for rule in FORMATTER_RULES:
        lines = rule.apply(tree, lines, path)
        formatted_text = "".join(lines)

        # Re-parse the updated text
        try:
            tree = ast.parse(formatted_text)
        except SyntaxError as e:
            LOG.error(
                "Syntax error after %s at %s[%d:%d]: %r -> %s",
                rule.__class__.__name__, path, e.lineno, e.offset, (e.text or "").rstrip(), e.msg
            )
            success = False
            break

    if success and formatted_text != original_text:
        with path.open("w", encoding = "utf-8", newline = "") as wf:
            wf.write(formatted_text)
        LOG.info("Formatted [%s].", path)


if __name__ == "__main__":
    if len(sys.argv) < 2:  # noqa: PLR2004 Magic value used in comparison
        script_path = Path(sys.argv[0])
        print(f"Usage: python {script_path} <directory1> [<directory2> ...]")
        sys.exit(1)

    for dir_arg in sys.argv[1:]:
        root = Path(dir_arg)
        if not root.exists():
            LOG.warning("Directory [%s] does not exist, skipping...", root)
            continue
        for py_file in root.rglob("*.py"):
            format_file(py_file)
