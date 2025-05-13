# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from gettext import gettext as _
from typing import Any, cast

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
        from_attributes:bool | None = None,
        context:Any | None = None,
        by_alias:bool | None = None,
        by_name:bool | None = None,
    ) -> Self:
        """
        Proxy to BaseModel.model_validate, but on error re‐raise as
        ContextualValidationError including the passed context.
        """
        try:
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


def __get_message_template(error_code:str) -> str | None:
    # https://github.com/pydantic/pydantic-core/blob/d03bf4a01ca3b378cc8590bd481f307e82115bc6/src/errors/types.rs#L477
    # ruff: noqa: PLR0911 Too many return statements
    # ruff: noqa: PLR0912 Too many branches
    # ruff: noqa: E701 Multiple statements on one line (colon)
    match error_code:
        case "no_such_attribute": return _("Object has no attribute '{attribute}'")
        case "json_invalid": return _("Invalid JSON: {error}")
        case "json_type": return _("JSON input should be string, bytes or bytearray")
        case "needs_python_object": return _("Cannot check `{method_name}` when validating from json, use a JsonOrPython validator instead")
        case "recursion_loop": return _("Recursion error - cyclic reference detected")
        case "missing": return _("Field required")
        case "frozen_field": return _("Field is frozen")
        case "frozen_instance": return _("Instance is frozen")
        case "extra_forbidden": return _("Extra inputs are not permitted")
        case "invalid_key": return _("Keys should be strings")
        case "get_attribute_error": return _("Error extracting attribute: {error}")
        case "model_type": return _("Input should be a valid dictionary or instance of {class_name}")
        case "model_attributes_type": return _("Input should be a valid dictionary or object to extract fields from")
        case "dataclass_type": return _("Input should be a dictionary or an instance of {class_name}")
        case "dataclass_exact_type": return _("Input should be an instance of {class_name}")
        case "none_required": return _("Input should be None")
        case "greater_than": return _("Input should be greater than {gt}")
        case "greater_than_equal": return _("Input should be greater than or equal to {ge}")
        case "less_than": return _("Input should be less than {lt}")
        case "less_than_equal": return _("Input should be less than or equal to {le}")
        case "multiple_of": return _("Input should be a multiple of {multiple_of}")
        case "finite_number": return _("Input should be a finite number")
        case "too_short": return _("{field_type} should have at least {min_length} item{expected_plural} after validation, not {actual_length}")
        case "too_long": return _("{field_type} should have at most {max_length} item{expected_plural} after validation, not {actual_length}")
        case "iterable_type": return _("Input should be iterable")
        case "iteration_error": return _("Error iterating over object, error: {error}")
        case "string_type": return _("Input should be a valid string")
        case "string_sub_type": return _("Input should be a string, not an instance of a subclass of str")
        case "string_unicode": return _("Input should be a valid string, unable to parse raw data as a unicode string")
        case "string_too_short": return _("String should have at least {min_length} character{expected_plural}")
        case "string_too_long": return _("String should have at most {max_length} character{expected_plural}")
        case "string_pattern_mismatch": return _("String should match pattern '{pattern}'")
        case "enum": return _("Input should be {expected}")
        case "dict_type": return _("Input should be a valid dictionary")
        case "mapping_type": return _("Input should be a valid mapping, error: {error}")
        case "list_type": return _("Input should be a valid list")
        case "tuple_type": return _("Input should be a valid tuple")
        case "set_type": return _("Input should be a valid set")
        case "set_item_not_hashable": return _("Set items should be hashable")
        case "bool_type": return _("Input should be a valid boolean")
        case "bool_parsing": return _("Input should be a valid boolean, unable to interpret input")
        case "int_type": return _("Input should be a valid integer")
        case "int_parsing": return _("Input should be a valid integer, unable to parse string as an integer")
        case "int_from_float": return _("Input should be a valid integer, got a number with a fractional part")
        case "int_parsing_size": return _("Unable to parse input string as an integer, exceeded maximum size")
        case "float_type": return _("Input should be a valid number")
        case "float_parsing": return _("Input should be a valid number, unable to parse string as a number")
        case "bytes_type": return _("Input should be a valid bytes")
        case "bytes_too_short": return _("Data should have at least {min_length} byte{expected_plural}")
        case "bytes_too_long": return _("Data should have at most {max_length} byte{expected_plural}")
        case "bytes_invalid_encoding": return _("Data should be valid {encoding}: {encoding_error}")
        case "value_error": return _("Value error, {error}")
        case "assertion_error": return _("Assertion failed, {error}")
        case "custom_error": return None  # handled separately
        case "literal_error": return _("Input should be {expected}")
        case "date_type": return _("Input should be a valid date")
        case "date_parsing": return _("Input should be a valid date in the format YYYY-MM-DD, {error}")
        case "date_from_datetime_parsing": return _("Input should be a valid date or datetime, {error}")
        case "date_from_datetime_inexact": return _("Datetimes provided to dates should have zero time - e.g. be exact dates")
        case "date_past": return _("Date should be in the past")
        case "date_future": return _("Date should be in the future")
        case "time_type": return _("Input should be a valid time")
        case "time_parsing": return _("Input should be in a valid time format, {error}")
        case "datetime_type": return _("Input should be a valid datetime")
        case "datetime_parsing": return _("Input should be a valid datetime, {error}")
        case "datetime_object_invalid": return _("Invalid datetime object, got {error}")
        case "datetime_from_date_parsing": return _("Input should be a valid datetime or date, {error}")
        case "datetime_past": return _("Input should be in the past")
        case "datetime_future": return _("Input should be in the future")
        case "timezone_naive": return _("Input should not have timezone info")
        case "timezone_aware": return _("Input should have timezone info")
        case "timezone_offset": return _("Timezone offset of {tz_expected} required, got {tz_actual}")
        case "time_delta_type": return _("Input should be a valid timedelta")
        case "time_delta_parsing": return _("Input should be a valid timedelta, {error}")
        case "frozen_set_type": return _("Input should be a valid frozenset")
        case "is_instance_of": return _("Input should be an instance of {class}")
        case "is_subclass_of": return _("Input should be a subclass of {class}")
        case "callable_type": return _("Input should be callable")
        case "union_tag_invalid": return _("Input tag '{tag}' found using {discriminator} does not match any of the expected tags: {expected_tags}")
        case "union_tag_not_found": return _("Unable to extract tag using discriminator {discriminator}")
        case "arguments_type": return _("Arguments must be a tuple, list or a dictionary")
        case "missing_argument": return _("Missing required argument")
        case "unexpected_keyword_argument": return _("Unexpected keyword argument")
        case "missing_keyword_only_argument": return _("Missing required keyword only argument")
        case "unexpected_positional_argument": return _("Unexpected positional argument")
        case "missing_positional_only_argument": return _("Missing required positional only argument")
        case "multiple_argument_values": return _("Got multiple values for argument")
        case "url_type": return _("URL input should be a string or URL")
        case "url_parsing": return _("Input should be a valid URL, {error}")
        case "url_syntax_violation": return _("Input violated strict URL syntax rules, {error}")
        case "url_too_long": return _("URL should have at most {max_length} character{expected_plural}")
        case "url_scheme": return _("URL scheme should be {expected_schemes}")
        case "uuid_type": return _("UUID input should be a string, bytes or UUID object")
        case "uuid_parsing": return _("Input should be a valid UUID, {error}")
        case "uuid_version": return _("UUID version {expected_version} expected")
        case "decimal_type": return _("Decimal input should be an integer, float, string or Decimal object")
        case "decimal_parsing": return _("Input should be a valid decimal")
        case "decimal_max_digits": return _("Decimal input should have no more than {max_digits} digit{expected_plural} in total")
        case "decimal_max_places": return _("Decimal input should have no more than {decimal_places} decimal place{expected_plural}")
        case "decimal_whole_digits": return _("Decimal input should have no more than {whole_digits} digit{expected_plural} before the decimal point")
        case "complex_type": return _("Input should be a valid python complex object, a number, or a valid complex string following the rules at https://docs.python.org/3/library/functions.html#complex")
        case "complex_str_parsing": return _("Input should be a valid complex string following the rules at https://docs.python.org/3/library/functions.html#complex")
        case _: return None
