# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the pydantics utilities module.

Covers ContextualValidationError, ContextualModel, and format_validation_error.
"""

from typing import Any, TypedDict, cast

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails as PydanticErrorDetails
from typing_extensions import NotRequired

from kleinanzeigen_bot.utils.pydantics import (
    ContextualModel,
    ContextualValidationError,
    format_validation_error,
)


class ErrorDetails(TypedDict):
    loc:tuple[str, ...]
    msg:str
    type:str
    input:NotRequired[Any]
    ctx:NotRequired[dict[str, Any]]

# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def context() -> dict[str, Any]:
    """Fixture for a sample context."""
    return {"user": "test", "reason": "unit-test"}


# --------------------------------------------------------------------------- #
# Test cases
# --------------------------------------------------------------------------- #


class TestContextualValidationError:
    """Test ContextualValidationError behavior."""

    def test_context_attached(self, context:dict[str, Any]) -> None:
        """Context is attached to the exception."""
        ex = ContextualValidationError("test", [])
        ex.context = context
        assert ex.context == context

    def test_context_missing(self) -> None:
        """Context is missing (default)."""
        ex = ContextualValidationError("test", [])
        assert not hasattr(ex, "context") or ex.context is None


class TestContextualModel:
    """Test ContextualModel validation logic."""

    class SimpleModel(ContextualModel):  # type: ignore[unused-ignore,misc]
        x:int

    def test_model_validate_success(self) -> None:
        """Valid input returns a model instance."""
        result = self.SimpleModel.model_validate({"x": 42})
        assert isinstance(result, self.SimpleModel)
        assert result.x == 42

    def test_model_validate_failure_with_context(self, context:dict[str, Any]) -> None:
        """Invalid input raises ContextualValidationError with context."""
        with pytest.raises(ContextualValidationError) as exc_info:
            self.SimpleModel.model_validate({"x": "not-an-int"}, context = context)
        assert exc_info.value.context == context


class TestFormatValidationError:
    """Test format_validation_error output."""

    class SimpleModel(BaseModel):
        y:int

    @pytest.mark.parametrize(
        ("error_details", "expected"),
        [
            # Standard error with known code and context
            (
                [{"loc": ("foo",), "msg": "dummy", "type": "int_parsing", "ctx": {}}],
                "Input should be a valid integer, unable to parse string as an integer",
            ),
            # Error with context variable
            (
                [{"loc": ("bar",), "msg": "dummy", "type": "greater_than", "ctx": {"gt": 5}}],
                "greater than 5",
            ),
            # Error with unknown code
            (
                [{"loc": ("baz",), "msg": "dummy", "type": "unknown_code"}],
                "[type=unknown_code]",
            ),
            # Error with message template containing ' or '
            (
                [{"loc": ("qux",), "msg": "dummy", "type": "enum", "ctx": {"expected": "'a' or 'b'"}}],
                "' or '",
            ),
            # Error with no context
            (
                [{"loc": ("nocontext",), "msg": "dummy", "type": "string_type"}],
                "Input should be a valid string",
            ),
            # Date/time related errors
            (
                [{"loc": ("date",), "msg": "dummy", "type": "date_parsing", "ctx": {"error": "invalid format"}}],
                "Input should be a valid date in the format YYYY-MM-DD",
            ),
            (
                [{"loc": ("datetime",), "msg": "dummy", "type": "datetime_parsing", "ctx": {"error": "invalid format"}}],
                "Input should be a valid datetime",
            ),
            (
                [{"loc": ("time",), "msg": "dummy", "type": "time_parsing", "ctx": {"error": "invalid format"}}],
                "Input should be in a valid time format",
            ),
            # URL related errors
            (
                [{"loc": ("url",), "msg": "dummy", "type": "url_parsing", "ctx": {"error": "invalid format"}}],
                "Input should be a valid URL",
            ),
            (
                [{"loc": ("url_scheme",), "msg": "dummy", "type": "url_scheme", "ctx": {"expected_schemes": "http,https"}}],
                "URL scheme should be http,https",
            ),
            # UUID related errors
            (
                [{"loc": ("uuid",), "msg": "dummy", "type": "uuid_parsing", "ctx": {"error": "invalid format"}}],
                "Input should be a valid UUID",
            ),
            (
                [{"loc": ("uuid_version",), "msg": "dummy", "type": "uuid_version", "ctx": {"expected_version": 4}}],
                "UUID version 4 expected",
            ),
            # Decimal related errors
            (
                [{"loc": ("decimal",), "msg": "dummy", "type": "decimal_parsing"}],
                "Input should be a valid decimal",
            ),
            (
                [{"loc": ("decimal_max_digits",), "msg": "dummy", "type": "decimal_max_digits", "ctx": {"max_digits": 10, "expected_plural": "s"}}],
                "Decimal input should have no more than 10 digits in total",
            ),
            # Complex number related errors
            (
                [{"loc": ("complex",), "msg": "dummy", "type": "complex_type"}],
                "Input should be a valid python complex object",
            ),
            (
                [{"loc": ("complex_str",), "msg": "dummy", "type": "complex_str_parsing"}],
                "Input should be a valid complex string",
            ),
            # List/sequence related errors
            (
                [{"loc": ("list",), "msg": "dummy", "type": "list_type"}],
                "Input should be a valid list",
            ),
            (
                [{"loc": ("tuple",), "msg": "dummy", "type": "tuple_type"}],
                "Input should be a valid tuple",
            ),
            (
                [{"loc": ("set",), "msg": "dummy", "type": "set_type"}],
                "Input should be a valid set",
            ),
            # String related errors
            (
                [{"loc": ("string_pattern",), "msg": "dummy", "type": "string_pattern_mismatch", "ctx": {"pattern": r"\d+"}}],
                "String should match pattern '\\d+'",
            ),
            (
                [{"loc": ("string_length",), "msg": "dummy", "type": "string_too_short", "ctx": {"min_length": 5, "expected_plural": "s"}}],
                "String should have at least 5 characters",
            ),
            # Number related errors
            (
                [{"loc": ("float",), "msg": "dummy", "type": "float_type"}],
                "Input should be a valid number",
            ),
            (
                [{"loc": ("int",), "msg": "dummy", "type": "int_type"}],
                "Input should be a valid integer",
            ),
            # Boolean related errors
            (
                [{"loc": ("bool",), "msg": "dummy", "type": "bool_type"}],
                "Input should be a valid boolean",
            ),
            (
                [{"loc": ("bool_parsing",), "msg": "dummy", "type": "bool_parsing"}],
                "Input should be a valid boolean, unable to interpret input",
            ),
        ],
    )
    def test_various_error_codes(self, error_details:list[dict[str, Any]], expected:str) -> None:
        """Test various error codes and message formatting."""
        class DummyValidationError(ValidationError):
            def errors(self, *, include_url:bool = True, include_context:bool = True, include_input:bool = True) -> list[PydanticErrorDetails]:
                return cast(list[PydanticErrorDetails], error_details)

            def error_count(self) -> int:
                return len(error_details)

            @property
            def title(self) -> str:
                return "Dummy"
        ex = DummyValidationError("dummy", [])
        out = format_validation_error(ex)
        assert any(exp in out for exp in expected.split()), f"Expected '{expected}' in output: {out}"

    def test_format_standard_validation_error(self) -> None:
        """Standard ValidationError produces expected string."""
        try:
            self.SimpleModel(y = "not an int")  # type: ignore[arg-type]
        except ValidationError as ex:
            out = format_validation_error(ex)
            assert "validation error" in out
            assert "y" in out
            assert "integer" in out

    def test_format_contextual_validation_error(self, context:dict[str, Any]) -> None:
        """ContextualValidationError includes context in output."""
        class Model(ContextualModel):  # type: ignore[unused-ignore,misc]
            z:int
        with pytest.raises(ContextualValidationError) as exc_info:
            Model.model_validate({"z": "not an int"}, context = context)
        assert exc_info.value.context == context

    def test_format_unknown_error_code(self) -> None:
        """Unknown error code falls back to default formatting."""
        class DummyValidationError(ValidationError):
            def errors(self, *, include_url:bool = True, include_context:bool = True, include_input:bool = True) -> list[PydanticErrorDetails]:
                return cast(list[PydanticErrorDetails], [{"loc": ("foo",), "msg": "dummy", "type": "unknown_code", "input": None}])

            def error_count(self) -> int:
                return 1

            @property
            def title(self) -> str:
                return "Dummy"
        ex = DummyValidationError("dummy", [])
        out = format_validation_error(ex)
        assert "foo" in out
        assert "dummy" in out
        assert "[type=unknown_code]" in out

    def test_pluralization_and_empty_errors(self) -> None:
        """Test pluralization in header and empty error list edge case."""
        class DummyValidationError(ValidationError):
            def errors(self, *, include_url:bool = True, include_context:bool = True, include_input:bool = True) -> list[PydanticErrorDetails]:
                return cast(list[PydanticErrorDetails], [
                    {"loc": ("a",), "msg": "dummy", "type": "int_type"},
                    {"loc": ("b",), "msg": "dummy", "type": "int_type"},
                ])

            def error_count(self) -> int:
                return 2

            @property
            def title(self) -> str:
                return "Dummy"
        ex1 = DummyValidationError("dummy", [])
        out = format_validation_error(ex1)
        assert "2 validation errors" in out
        assert "a" in out
        assert "b" in out

        # Empty error list
        class EmptyValidationError(ValidationError):
            def errors(self, *, include_url:bool = True, include_context:bool = True, include_input:bool = True) -> list[PydanticErrorDetails]:
                return cast(list[PydanticErrorDetails], [])

            def error_count(self) -> int:
                return 0

            @property
            def title(self) -> str:
                return "Empty"
        ex2 = EmptyValidationError("empty", [])
        out = format_validation_error(ex2)
        assert "0 validation errors" in out
        assert out.count("-") == 0
