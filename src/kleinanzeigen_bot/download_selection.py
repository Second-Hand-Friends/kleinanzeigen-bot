# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import re
from typing import Any, Final, Mapping, NamedTuple

__all__ = [
    "NUMERIC_IDS_RE",
    "ResolvedAdState",
    "resolve_download_ad_activity",
]

NUMERIC_IDS_RE:Final[re.Pattern[str]] = re.compile(r"^\d+(,\d+)*$")


class ResolvedAdState(NamedTuple):
    """Resolved download state for a published ad."""

    active:bool
    owned:bool


def resolve_download_ad_activity(ad_id:int, published_ads_by_id:Mapping[int, Mapping[str, Any]]) -> ResolvedAdState:
    """Resolve whether a downloaded ad should be marked active and owned."""
    published_ad = published_ads_by_id.get(ad_id)
    if published_ad is None:
        return ResolvedAdState(active = False, owned = False)

    return ResolvedAdState(active = published_ad.get("state") == "active", owned = True)
