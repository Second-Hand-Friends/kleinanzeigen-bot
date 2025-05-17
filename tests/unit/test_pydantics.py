# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the pydantics utilities module.

Covers ContextualValidationError, ContextualModel, and format_validation_error.
"""

from typing import Any, TypedDict

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
    loc: tuple[str, ...]
    msg: str
    type: str
    input: NotRequired[Any]

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

    def test_context_attached(self, context: dict[str, Any]) -> None:
        """Context is attached to the exception."""
        ex = ContextualValidationError("test", [])
        ex.context = context
        assert ex.context == context


class TestContextualModel:
    """Test ContextualModel validation logic."""

    class SimpleModel(ContextualModel):
        x: int

    def test_model_validate_success(self) -> None:
        """Valid input returns a model instance."""
        result = self.SimpleModel.model_validate({"x": 42})
        assert isinstance(result, self.SimpleModel)
        assert result.x == 42

    def test_model_validate_failure_with_context(self, context: dict[str, Any]) -> None:
        """Invalid input raises ContextualValidationError with context."""
        with pytest.raises(ContextualValidationError) as exc_info:
            self.SimpleModel.model_validate({"x": "not-an-int"}, context=context)
        assert exc_info.value.context == context


class TestFormatValidationError:
    """Test format_validation_error output."""

    class SimpleModel(BaseModel):
        y: int

    def test_format_standard_validation_error(self) -> None:
        """Standard ValidationError produces expected string."""
        try:
            self.SimpleModel(y=42)
        except ValidationError as ex:
            out = format_validation_error(ex)
            assert "validation error" in out
            assert "y" in out
            assert "integer" in out

    def test_format_contextual_validation_error(self, context: dict[str, Any]) -> None:
        """ContextualValidationError includes context in output."""

        class Model(ContextualModel):  # type: ignore[unused-ignore,misc]
            z: int

        with pytest.raises(ContextualValidationError) as exc_info:
            Model.model_validate({"z": "not an int"}, context=context)
        assert exc_info.value.context == context

    def test_format_unknown_error_code(self) -> None:
        """Unknown error code falls back to default formatting."""

        class DummyValidationError(ValidationError):
            def errors(self, *, include_url: bool = True, include_context: bool = True, include_input: bool = True) -> list[PydanticErrorDetails]:
                return [{"loc": ("foo",), "msg": "dummy", "type": "unknown_code", "input": None}]

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
