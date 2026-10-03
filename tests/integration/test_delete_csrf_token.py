# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Read the delete flow's CSRF token from classic and redesigned manage-ads markup in a real browser."""

import html
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from kleinanzeigen_bot import delete_flow
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

# the browser is shared across the session, see conftest.py
pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

_ROOT_URL = "https://www.kleinanzeigen.de"
_CLASSIC_META = '<meta name="_csrf" content="classic-token"><meta name="_csrf_header" content="X-CSRF-TOKEN">'


def _initialprops(props:object) -> str:
    """Return a sanitized copy of the redesigned page's consent banner carrying the given props."""
    return f'<div id="consentBanner" data-initialprops="{html.escape(json.dumps(props))}" class="gdpr-cmp"></div>'


_ASTRO_BANNER = _initialprops({
    "theme": "desktop",
    "googleAnalyticsOpts": {"eventCategory": "AstroPageSkeletonWeb", "userId": "XXX", "dimensions": {}},
    "webContext": {"baseUrl": _ROOT_URL, "csrfToken": "astro-token", "userId": "XXX"},
    "features": {},
})


@pytest.mark.parametrize(
    ("head", "body", "expected_token"),
    [
        (_CLASSIC_META, "", "classic-token"),
        ("", _ASTRO_BANNER, "astro-token"),
        ('<meta name="_csrf" content="  ">', _ASTRO_BANNER, "astro-token"),
        ("", '<div data-initialprops="{not json"></div>' + _ASTRO_BANNER, "astro-token"),
        ("", _initialprops({"webContext": {"csrfToken": ""}}), None),
        ("", "", None),
    ],
    ids = ["classic-meta", "astro-initialprops", "blank-meta-falls-back", "skips-malformed-props", "blank-astro-token", "no-token"],
)
async def test_delete_ad_sends_csrf_token_from_meta_or_astro_initialprops(
    web:WebScrapingMixin, base_ad_config:dict[str, Any], head:str, body:str, expected_token:str | None,
) -> None:
    """The classic meta tag wins; the redesigned page's webContext.csrfToken is the fallback (#1312)."""
    page = f"<!DOCTYPE html><html><head>{head}</head><body>{body}<p id='loaded'></p></body></html>"
    await web.web_execute(f"""(() => {{
        document.open();
        document.write({json.dumps(page)});
        document.close();
    }})()""")
    await web.web_await(
        lambda: web.web_execute("document.getElementById('loaded') !== null"),
        timeout = 10,
        timeout_error_message = "Offline manage-ads fixture did not load",
    )
    ad_cfg = Ad.model_validate(base_ad_config | {"id": 12345})
    ok_response = {"statusCode": 200, "statusMessage": "OK", "content": "{}"}

    # the offline page stands in for the manage-ads page; the delete request itself is never sent
    with (
        patch.object(web, "web_open", new_callable = AsyncMock),
        patch.object(web, "web_sleep", new_callable = AsyncMock),
        patch.object(web, "web_request", new_callable = AsyncMock, return_value = ok_response) as mock_request,
    ):
        if expected_token is None:
            with pytest.raises(AssertionError, match = "Expected CSRF Token not found"):
                await delete_flow.delete_ad(web, _ROOT_URL, ad_cfg, [], delete_old_ads_by_title = False)
            mock_request.assert_not_called()
        else:
            await delete_flow.delete_ad(web, _ROOT_URL, ad_cfg, [], delete_old_ads_by_title = False)
            assert mock_request.call_args.kwargs["headers"] == {"x-csrf-token": expected_token}
