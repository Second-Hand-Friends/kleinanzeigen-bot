# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Ad description composition (prefix, suffix, length validation)."""

__all__ = ["get_ad_description"]

from gettext import gettext as _

from kleinanzeigen_bot.model.ad_model import MAX_DESCRIPTION_LENGTH, Ad
from kleinanzeigen_bot.model.config_model import AdDefaults
from kleinanzeigen_bot.utils.misc import ensure


def get_ad_description(ad:Ad, defaults:AdDefaults, *, with_affixes:bool) -> str:
    """Build the final ad description with optional prefix/suffix affixes.

    Precedence (highest to lowest):
    1. Direct ad-level affixes (``description_prefix`` / ``description_suffix``)
    2. Global flattened affixes (``ad_defaults.description_prefix`` /
       ``description_suffix``). Legacy nested ``ad_defaults.description.prefix`` /
       ``.suffix`` values are migrated into the flattened form by the
       ``AdDefaults`` model validator, so this function only reads the
       flattened attributes.

    ``@`` characters in the final description are replaced with ``(at)``.

    Raises:
        AssertionError: If the final description exceeds ``MAX_DESCRIPTION_LENGTH``.
    """
    description_text = ad.description or ""

    if with_affixes:
        prefix = ad.description_prefix if ad.description_prefix is not None else defaults.description_prefix or ""
        suffix = ad.description_suffix if ad.description_suffix is not None else defaults.description_suffix or ""
        final_description = str(prefix) + str(description_text) + str(suffix)
        final_description = final_description.replace("@", "(at)")
    else:
        final_description = description_text

    ensure(
        len(final_description) <= MAX_DESCRIPTION_LENGTH,
        _(f"Length of ad description including prefix and suffix exceeds {MAX_DESCRIPTION_LENGTH} chars. Description length: {len(final_description)} chars."),  # noqa: INT001
    )

    return final_description
