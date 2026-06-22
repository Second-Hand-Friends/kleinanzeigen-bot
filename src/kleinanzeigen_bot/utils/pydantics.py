# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from gettext import gettext as _
from typing import Any, Literal, cast

from pydantic import BaseModel, ValidationError
from pydantic_core import InitErrorDetails
from typing_extensions import Self

from kleinanzeigen_bot.utils.i18n import pluralize


class ContextualValidationError(ValidationError):
    context:Any


class ContextualModel(BaseModel):

    @classmethod
    def model_validate(
        cls,
        obj:Any,
        *,
        strict:bool | None = None,
        extra:Literal["allow", "ignore", "forbid"] | None = None,
        from_attributes:bool | None = None,
        context:Any | None = None,
        by_alias:bool | None = None,
        by_name:bool | None = None,
    ) -> Self:
        """
        Proxy to BaseModel.model_validate, but on error re‐raise as
        ContextualValidationError including the passed context.

        Note: Pydantic v2 does not support call-time `extra=...`; this argument
        is accepted for backward-compatibility but ignored.
        """
        try:
            _ = extra  # kept for backward-compatibility; intentionally ignored
            return super().model_validate(
                obj,
                strict = strict,
                from_attributes = from_attributes,
                context = context,
                by_alias = by_alias,
                by_name = by_name,
            )
        except ValidationError as ex:
            new_ex = ContextualValidationError.from_exception_data(
                title = ex.title,
                line_errors = cast(list[InitErrorDetails], ex.errors()),
            )
            new_ex.context = context
            raise new_ex from ex


def format_validation_error(ex:ValidationError) -> str:
    """
    Turn a Pydantic ValidationError into the classic:
      N validation errors for ModelName
      field
        message [type=code]

    >>> from pydantic import BaseModel, ValidationError
    >>> class M(BaseModel): x: int
    >>> try:
    ...     M(x="no-int")
    ... except ValidationError as e:
    ...     print(format_validation_error(e))
    1 validation error for [M]:
    - x: Input should be a valid integer, unable to parse string as an integer
    """
    errors = ex.errors(include_url = False, include_input = False, include_context = True)
    ctx = ex.context if isinstance(ex, ContextualValidationError) and ex.context else ex.title
    header = _("%s for [%s]:") % (pluralize("validation error", ex.error_count()), ctx)
    lines = [header]
    for err in errors:
        loc = ".".join(str(p) for p in err["loc"])
        msg_ctx = err.get("ctx")
        code = err["type"]
        msg_template = __get_message_template(code)
        if msg_template:
            msg = _(msg_template).format(**msg_ctx) if msg_ctx else msg_template
            msg = msg.replace("' or '", _("' or '"))
            lines.append(f"- {loc}: {msg}")
        else:
            lines.append(f"- {loc}: {err['msg']} [type={code}]")
    return "\n".join(lines)


# Mapping of pydantic error codes -> raw English message templates.
# "custom_error" intentionally omitted; caller falls back to Pydantic's message.
# Strings are NOT wrapped with _() here — translation happens lazily in
# __get_message_template to avoid freezing the locale at import time.
# See https://github.com/pydantic/pydantic-core/blob/main/src/errors/types.rs
_MESSAGE_TEMPLATES:dict[str, str] = {
    "no_such_attribute": "Object has no attribute '{attribute}'",
    "json_invalid": "Invalid JSON: {error}",
    "json_type": "JSON input should be string, bytes or bytearray",
    "needs_python_object": "Cannot check `{method_name}` when validating from json, use a JsonOrPython validator instead",
    "recursion_loop": "Recursion error - cyclic reference detected",
    "missing": "Field required",
    "frozen_field": "Field is frozen",
    "frozen_instance": "Instance is frozen",
    "extra_forbidden": "Extra inputs are not permitted",
    "invalid_key": "Keys should be strings",
    "get_attribute_error": "Error extracting attribute: {error}",
    "model_type": "Input should be a valid dictionary or instance of {class_name}",
    "model_attributes_type": "Input should be a valid dictionary or object to extract fields from",
    "dataclass_type": "Input should be a dictionary or an instance of {class_name}",
    "dataclass_exact_type": "Input should be an instance of {class_name}",
    "none_required": "Input should be None",
    "greater_than": "Input should be greater than {gt}",
    "greater_than_equal": "Input should be greater than or equal to {ge}",
    "less_than": "Input should be less than {lt}",
    "less_than_equal": "Input should be less than or equal to {le}",
    "multiple_of": "Input should be a multiple of {multiple_of}",
    "finite_number": "Input should be a finite number",
    "too_short": "{field_type} should have at least {min_length} item{expected_plural} after validation, not {actual_length}",
    "too_long": "{field_type} should have at most {max_length} item{expected_plural} after validation, not {actual_length}",
    "iterable_type": "Input should be iterable",
    "iteration_error": "Error iterating over object, error: {error}",
    "string_type": "Input should be a valid string",
    "string_sub_type": "Input should be a string, not an instance of a subclass of str",
    "string_unicode": "Input should be a valid string, unable to parse raw data as a unicode string",
    "string_too_short": "String should have at least {min_length} character{expected_plural}",
    "string_too_long": "String should have at most {max_length} character{expected_plural}",
    "string_pattern_mismatch": "String should match pattern '{pattern}'",
    "enum": "Input should be {expected}",
    "dict_type": "Input should be a valid dictionary",
    "mapping_type": "Input should be a valid mapping, error: {error}",
    "list_type": "Input should be a valid list",
    "tuple_type": "Input should be a valid tuple",
    "set_type": "Input should be a valid set",
    "set_item_not_hashable": "Set items should be hashable",
    "bool_type": "Input should be a valid boolean",
    "bool_parsing": "Input should be a valid boolean, unable to interpret input",
    "int_type": "Input should be a valid integer",
    "int_parsing": "Input should be a valid integer, unable to parse string as an integer",
    "int_from_float": "Input should be a valid integer, got a number with a fractional part",
    "int_parsing_size": "Unable to parse input string as an integer, exceeded maximum size",
    "float_type": "Input should be a valid number",
    "float_parsing": "Input should be a valid number, unable to parse string as a number",
    "bytes_type": "Input should be a valid bytes",
    "bytes_too_short": "Data should have at least {min_length} byte{expected_plural}",
    "bytes_too_long": "Data should have at most {max_length} byte{expected_plural}",
    "bytes_invalid_encoding": "Data should be valid {encoding}: {encoding_error}",
    "value_error": "Value error, {error}",
    "assertion_error": "Assertion failed, {error}",
    # "custom_error" omitted intentionally — caller falls back to Pydantic's own message
    "literal_error": "Input should be {expected}",
    "date_type": "Input should be a valid date",
    "date_parsing": "Input should be a valid date in the format YYYY-MM-DD, {error}",
    "date_from_datetime_parsing": "Input should be a valid date or datetime, {error}",
    "date_from_datetime_inexact": "Datetimes provided to dates should have zero time - e.g. be exact dates",
    "date_past": "Date should be in the past",
    "date_future": "Date should be in the future",
    "time_type": "Input should be a valid time",
    "time_parsing": "Input should be in a valid time format, {error}",
    "datetime_type": "Input should be a valid datetime",
    "datetime_parsing": "Input should be a valid datetime, {error}",
    "datetime_object_invalid": "Invalid datetime object, got {error}",
    "datetime_from_date_parsing": "Input should be a valid datetime or date, {error}",
    "datetime_past": "Input should be in the past",
    "datetime_future": "Input should be in the future",
    "timezone_naive": "Input should not have timezone info",
    "timezone_aware": "Input should have timezone info",
    "timezone_offset": "Timezone offset of {tz_expected} required, got {tz_actual}",
    "time_delta_type": "Input should be a valid timedelta",
    "time_delta_parsing": "Input should be a valid timedelta, {error}",
    "frozen_set_type": "Input should be a valid frozenset",
    "is_instance_of": "Input should be an instance of {class}",
    "is_subclass_of": "Input should be a subclass of {class}",
    "callable_type": "Input should be callable",
    "union_tag_invalid": "Input tag '{tag}' found using {discriminator} does not match any of the expected tags: {expected_tags}",
    "union_tag_not_found": "Unable to extract tag using discriminator {discriminator}",
    "arguments_type": "Arguments must be a tuple, list or a dictionary",
    "missing_argument": "Missing required argument",
    "unexpected_keyword_argument": "Unexpected keyword argument",
    "missing_keyword_only_argument": "Missing required keyword only argument",
    "unexpected_positional_argument": "Unexpected positional argument",
    "missing_positional_only_argument": "Missing required positional only argument",
    "multiple_argument_values": "Got multiple values for argument",
    "url_type": "URL input should be a string or URL",
    "url_parsing": "Input should be a valid URL, {error}",
    "url_syntax_violation": "Input violated strict URL syntax rules, {error}",
    "url_too_long": "URL should have at most {max_length} character{expected_plural}",
    "url_scheme": "URL scheme should be {expected_schemes}",
    "uuid_type": "UUID input should be a string, bytes or UUID object",
    "uuid_parsing": "Input should be a valid UUID, {error}",
    "uuid_version": "UUID version {expected_version} expected",
    "decimal_type": "Decimal input should be an integer, float, string or Decimal object",
    "decimal_parsing": "Input should be a valid decimal",
    "decimal_max_digits": "Decimal input should have no more than {max_digits} digit{expected_plural} in total",
    "decimal_max_places": "Decimal input should have no more than {decimal_places} decimal place{expected_plural}",
    "decimal_whole_digits": "Decimal input should have no more than {whole_digits} digit{expected_plural} before the decimal point",
    "complex_type": (
        "Input should be a valid python complex object, a number, or a valid complex string "
        "following the rules at https://docs.python.org/3/library/functions.html#complex"
    ),
    "complex_str_parsing": (
        "Input should be a valid complex string following the rules at "
        "https://docs.python.org/3/library/functions.html#complex"
    ),
}


def __get_message_template(error_code:str) -> str | None:
    template = _MESSAGE_TEMPLATES.get(error_code)
    return _(template) if template is not None else None
